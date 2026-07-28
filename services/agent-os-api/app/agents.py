"""
Agents — agent.critic e agent.researcher.

v2.0 — correção arquitetural em relação ao v0.1.1 (ver ADR-0013):

1. `agent.critic` vivia dentro de dispatcher.py, não aqui. Isso não é
   só uma questão de organização de pasta: um Agent que vive dentro
   do dispatcher fica na posição errada da hierarquia
   Runtime→Agent→Skill→Tool (01-ARCHITECTURE/Contrato_Interfaces.md).
   Confirmado nesta reconstrução: o `agent.critic` da v0.1.1 chamava
   `call_llm()` DIRETAMENTE, pulando o dispatcher por completo — a
   mesma classe de violação do item_001 (Planner bypassando o
   dispatcher), só que num lugar diferente. Agora `agent.critic`
   mora aqui, na camada Agent, e fala com o motor de LLM através de
   uma Envelope para `tool.llm_call` via `dispatch()`, como qualquer
   Agent deveria.

2. `agent.researcher` (`run_researcher`) já fazia isso certo na
   v0.1.1 — usava `dispatch()` para tudo (`tool.llm_call`,
   `tool.calculator`, `skill.rag_search`). Portado sem mudança de
   comportamento. A diferença real aqui é que agora ele também é
   registrado no dispatcher via `register_handler()` — na v0.1.1 só
   existia um endpoint HTTP direto pra ele (`/v1/agent/researcher`),
   e o `target_id` "agent.researcher" que o Planner podia escolher
   NÃO tinha rota nenhuma no dispatcher (o bug mais grave encontrado
   na análise pré-reconstrução: qualquer plano que usasse
   agent.researcher falhava garantido com UNKNOWN_TARGET). Agora tem.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Optional

from app.dispatcher import DispatchResult, dispatch, register_handler
from app.schemas import Envelope, ErrorCode, Permissions, make_error, make_result

logger = logging.getLogger("agents")

_CALCULATOR_TOOL = {
    "type": "function",
    "function": {
        "name": "calculator",
        "description": (
            "Executa cálculos matemáticos de forma segura e determinística. "
            "Use SEMPRE esta ferramenta para qualquer operação aritmética, "
            "raiz quadrada, potência, ou análise de dígitos de números grandes. "
            "NUNCA calcule mentalmente."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": (
                        "Expressão matemática em Python. "
                        "Exemplos: '347 * 289', 'sqrt(2)', '2**1000', "
                        "'(18.50 * 23) * 0.88'"
                    ),
                }
            },
            "required": ["expression"],
        },
    },
}

_RAG_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "rag_search",
        "description": (
            "Busca informações na base de conhecimento do domínio especificado. "
            "Use quando a pergunta exige contexto específico ou dados que "
            "podem estar na base de conhecimento."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Texto da consulta em linguagem natural"}
            },
            "required": ["query"],
        },
    },
}

_RESEARCHER_SYSTEM_PROMPT = (
    "Você é um assistente de pesquisa inteligente. "
    "Use as ferramentas disponíveis quando necessário para responder com precisão. "
    "Para cálculos matemáticos, SEMPRE use a ferramenta calculator — nunca calcule mentalmente."
)


async def _call_llm_with_tools(
    root_trace_id: str,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    temperature: float = 0.1,
    max_tokens: Optional[int] = None,
) -> dict:
    """Chama tool.llm_call via dispatch() — todo Agent fala com o
    motor de LLM por aqui, nunca chamando app.llm_client diretamente."""
    payload: dict = {
        "messages": messages,
        "model": "lmstudio-community/qwen2.5-7b-instruct",
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    envelope = Envelope(
        trace_id=root_trace_id,
        parent_id=root_trace_id,
        layer_from="agent",
        layer_to="tool",
        target_id="tool.llm_call",
        payload=payload,
        permissions=Permissions(level="execute_sandboxed"),
    )
    response, _latency_ms, status = await dispatch(envelope)
    if status != "success":
        raise RuntimeError(f"tool.llm_call falhou: {response.payload.get('message')}")

    return response.payload["result"]


async def _rag_search(root_trace_id: str, query: str, domain: str) -> list[dict]:
    envelope = Envelope(
        trace_id=root_trace_id,
        parent_id=root_trace_id,
        layer_from="agent",
        layer_to="skill",
        target_id="skill.rag_search",
        payload={"query": query},
        context={"domain": domain},
        permissions=Permissions(level="read_only"),
    )
    response, _latency_ms, status = await dispatch(envelope)
    if status != "success":
        logger.warning("skill.rag_search falhou: %s — respondendo sem contexto", response.payload.get("message"))
        return []
    return response.payload["result"]["matches"]


async def _calculate(root_trace_id: str, expression: str) -> Optional[dict]:
    envelope = Envelope(
        trace_id=root_trace_id,
        parent_id=root_trace_id,
        layer_from="agent",
        layer_to="tool",
        target_id="tool.calculator",
        payload={"expression": expression},
        permissions=Permissions(level="read_only"),
    )
    response, _latency_ms, status = await dispatch(envelope)
    if status != "success":
        logger.warning("tool.calculator falhou: %s — expressao='%s'", response.payload.get("message"), expression)
        return None
    return response.payload["result"]


async def run_researcher(objective: str, domain: str) -> dict:
    """
    Fluxo com tool calling nativo:
    1. Envia pergunta + tools → LLM decide usar tool ou responder direto
    2. Se tool_calls: executa tool(s) → reenvia resultado → LLM gera resposta final
    3. Se não: responde direto
    """
    root_trace_id = str(uuid.uuid4())

    messages = [
        {"role": "system", "content": _RESEARCHER_SYSTEM_PROMPT},
        {"role": "user", "content": objective},
    ]

    raw_response = await _call_llm_with_tools(
        root_trace_id, messages=messages, tools=[_CALCULATOR_TOOL, _RAG_SEARCH_TOOL], temperature=0.1,
    )

    choice = raw_response.get("choices", [{}])[0]
    message = choice.get("message", {})
    finish_reason = choice.get("finish_reason")
    tool_calls = message.get("tool_calls", [])

    matches: list[dict] = []
    calc_result: Optional[dict] = None
    used_expression: Optional[str] = None

    if finish_reason == "tool_calls" and tool_calls:
        assistant_msg = {
            "role": "assistant",
            "content": message.get("content"),
            "tool_calls": [
                {"id": tc["id"], "type": tc["type"], "function": tc["function"]} for tc in tool_calls
            ],
        }
        messages.append(assistant_msg)

        for tool_call in tool_calls:
            tool_name = tool_call["function"]["name"]
            arguments = json.loads(tool_call["function"]["arguments"])

            if tool_name == "calculator":
                expression = arguments.get("expression", "")
                used_expression = expression
                calc_result = await _calculate(root_trace_id, expression)
                tool_result = {
                    "status": "success" if calc_result else "error",
                    "value": calc_result["value"] if calc_result else None,
                    "digit_count": calc_result.get("digit_count") if calc_result else None,
                    "first_digits": calc_result.get("first_digits") if calc_result else None,
                    "last_digits": calc_result.get("last_digits") if calc_result else None,
                    "digit_sum": calc_result.get("digit_sum") if calc_result else None,
                }
            elif tool_name == "rag_search":
                query = arguments.get("query", objective)
                matches = await _rag_search(root_trace_id, query=query, domain=domain)
                tool_result = {"status": "success", "matches_found": len(matches), "matches": matches}
            else:
                tool_result = {"status": "error", "message": f"Tool desconhecida: {tool_name}"}

            messages.append({
                "role": "tool", "tool_call_id": tool_call["id"], "content": json.dumps(tool_result),
            })

        final_response = await _call_llm_with_tools(root_trace_id, messages=messages, temperature=0.3)
        answer = final_response.get("choices", [{}])[0].get("message", {}).get("content", "")
    else:
        answer = message.get("content", "")

    return {
        "trace_id": root_trace_id,
        "objective": objective,
        "domain": domain,
        "action": "tool_call" if tool_calls else "direct",
        "used_search": bool(matches),
        "sources": [{"id": m["id"], "metadata": m["metadata"]} for m in matches],
        "calculation": (
            {"expression": used_expression, **calc_result} if calc_result is not None else None
        ),
        "answer": answer,
    }


async def _handle_researcher(envelope: Envelope, start: float) -> DispatchResult:
    objective = envelope.payload.get("objective")
    if not objective:
        latency_ms = (time.perf_counter() - start) * 1000
        return (
            make_error(envelope, ErrorCode.INVALID_INPUT, "payload.objective é obrigatório"),
            latency_ms,
            "error",
        )
    domain = envelope.context.get("domain", "matematica")

    result = await run_researcher(objective=objective, domain=domain)
    latency_ms = (time.perf_counter() - start) * 1000
    return (
        make_result(envelope, payload={"status": "success", "result": result}),
        latency_ms,
        "success",
    )


register_handler(
    "agent.researcher",
    {"execute_sandboxed", "execute_with_confirmation", "full_access"},
    _handle_researcher,
)


async def _handle_critic(envelope: Envelope, start: float) -> DispatchResult:
    """
    agent.critic — Revisa e valida resultados de outros agentes.

    payload esperado:
      - content: texto/resultado a ser revisado (obrigatório)
      - criteria: (opcional) critérios específicos de revisão

    result devolvido:
      - approval: true/false/None (None se o LLM não devolveu JSON válido)
      - feedback: texto com a análise
      - issues: lista de problemas encontrados
    """
    content = envelope.payload.get("content")
    if not content:
        latency_ms = (time.perf_counter() - start) * 1000
        return (
            make_error(envelope, ErrorCode.INVALID_INPUT, "payload.content é obrigatório para revisão"),
            latency_ms,
            "error",
        )

    criteria = envelope.payload.get("criteria", "Verifique precisão, coerência e completude.")
    root_trace_id = envelope.trace_id

    messages = [
        {
            "role": "system",
            "content": (
                "Você é um revisor crítico. Analise o conteúdo fornecido e retorne um JSON "
                "com: approval (boolean), feedback (string), issues (array de strings)."
            ),
        },
        {"role": "user", "content": f"Critérios: {criteria}\n\nConteúdo para revisar:\n{content}"},
    ]

    try:
        raw_result = await _call_llm_with_tools(
            root_trace_id, messages=messages, temperature=0.2, max_tokens=2000,
        )
    except RuntimeError as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return (
            make_error(envelope, ErrorCode.UPSTREAM_UNAVAILABLE, str(exc), recoverable=True),
            latency_ms,
            "error",
        )

    critic_text = raw_result.get("choices", [{}])[0].get("message", {}).get("content", "")

    try:
        critic_result = json.loads(critic_text)
    except json.JSONDecodeError:
        critic_result = {
            "approval": None,
            "feedback": critic_text,
            "issues": ["Resposta do critic não foi JSON válido"],
        }

    latency_ms = (time.perf_counter() - start) * 1000
    result = make_result(
        envelope,
        payload={
            "status": "success",
            "result": {
                "approval": critic_result.get("approval"),
                "feedback": critic_result.get("feedback", critic_text),
                "issues": critic_result.get("issues", []),
            },
        },
        meta={
            "tokens_input": raw_result.get("usage", {}).get("prompt_tokens"),
            "tokens_output": raw_result.get("usage", {}).get("completion_tokens"),
        },
    )
    return result, latency_ms, "success"


register_handler(
    "agent.critic",
    {"execute_sandboxed", "execute_with_confirmation", "full_access"},
    _handle_critic,
)
"""
agent.researcher — refatorado para tool calling nativo (OpenAI-compatible).

O LLM agora recebe tool definitions e decide via finish_reason="tool_calls",
em vez de parsing frágil de JSON no content.

Fluxo:
    1. Envia pergunta + tools → LLM decide usar tool ou não
    2. Se tool_calls: executa tool → reenvia resultado → LLM responde
    3. Se não: responde direto
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Optional

from app.dispatcher import dispatch
from app.llm_client import call_llm
from app.logging_utils import log_execution
from app.schemas import Envelope, Permissions

logger = logging.getLogger("agent.researcher")

# Tool definitions no formato OpenAI — LM Studio injeta no system prompt
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
                        "'(18.50 * 23) * 0.88', '123456789 ** 123456'"
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
                "query": {
                    "type": "string",
                    "description": "Texto da consulta em linguagem natural",
                }
            },
            "required": ["query"],
        },
    },
}

_SYSTEM_PROMPT = (
    "Você é um assistente de pesquisa inteligente. "
    "Use as ferramentas disponíveis quando necessário para responder com precisão. "
    "Para cálculos matemáticos, SEMPRE use a ferramenta calculator — nunca calcule mentalmente."
)


async def _call_llm_with_tools(
    root_trace_id: str,
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    temperature: float = 0.1,
    layer_to: str = "tool",
) -> dict:
    """
    Chama tool.llm_call via dispatcher, mas agora com suporte a tools.
    Retorna o raw_response completo (não só o content).
    """
    payload = {
        "messages": messages,
        "model": "Qwen2.5-7B-Instruct-Q4_K_M",
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    envelope = Envelope(
        trace_id=root_trace_id,
        parent_id=root_trace_id,
        layer_from="agent",
        layer_to=layer_to,
        target_id="tool.llm_call",
        payload=payload,
        permissions=Permissions(level="execute_sandboxed"),
    )
    start = time.perf_counter()
    response, latency_ms, status = await dispatch(envelope)
    log_execution(
        envelope,
        response.type,
        status,
        response.payload.get("error_code"),
        latency_ms,
        tokens_input=response.meta.get("tokens_input"),
        tokens_output=response.meta.get("tokens_output"),
    )
    if status != "success":
        raise RuntimeError(f"tool.llm_call falhou: {response.payload.get('message')}")

    # O dispatcher retorna o raw_response no campo result.content (JSON string)
    # Precisamos parsear de volta para dict
    raw_text = response.payload["result"]["content"]
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        # Se não for JSON válido, retorna como está (pode ser resposta direta)
        return {"choices": [{"message": {"content": raw_text, "role": "assistant"}}]}


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
    start = time.perf_counter()
    response, latency_ms, status = await dispatch(envelope)
    log_execution(
        envelope,
        response.type,
        status,
        response.payload.get("error_code"),
        latency_ms,
        tokens_input=response.meta.get("tokens_input"),
        tokens_output=response.meta.get("tokens_output"),
    )
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
    start = time.perf_counter()
    response, latency_ms, status = await dispatch(envelope)
    log_execution(
        envelope,
        response.type,
        status,
        response.payload.get("error_code"),
        latency_ms,
    )
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

    # ===== ETAPA 1: LLM decide se usa tool =====
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": objective},
    ]

    raw_response = await _call_llm_with_tools(
        root_trace_id,
        messages=messages,
        tools=[_CALCULATOR_TOOL, _RAG_SEARCH_TOOL],
        temperature=0.1,  # Determinístico para decisão
    )

    choice = raw_response.get("choices", [{}])[0]
    message = choice.get("message", {})
    finish_reason = choice.get("finish_reason")
    tool_calls = message.get("tool_calls", [])

    matches: list[dict] = []
    calc_result: Optional[dict] = None
    used_expression: Optional[str] = None

    # ===== ETAPA 2: Se usou tool, executa =====
    if finish_reason == "tool_calls" and tool_calls:
        # Adiciona a mensagem do assistant com tool_calls ao histórico
        assistant_msg = {
            "role": "assistant",
            "content": message.get("content"),
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": tc["type"],
                    "function": tc["function"],
                }
                for tc in tool_calls
            ],
        }
        messages.append(assistant_msg)

        # Executa cada tool call
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

                tool_result = {
                    "status": "success",
                    "matches_found": len(matches),
                    "matches": matches,
                }

            else:
                tool_result = {"status": "error", "message": f"Tool desconhecida: {tool_name}"}

            # Adiciona resultado da tool ao histórico
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": json.dumps(tool_result),
            })

        # ===== ETAPA 3: LLM gera resposta final com os resultados =====
        final_response = await _call_llm_with_tools(
            root_trace_id,
            messages=messages,
            temperature=0.3,  # Um pouco mais natural para resposta final
        )

        answer = final_response.get("choices", [{}])[0].get("message", {}).get("content", "")

    else:
        # Resposta direta (sem tool)
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

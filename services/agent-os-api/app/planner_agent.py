"""
agent.planner — o único Agent que pode criar TaskGraphs.

REGRA CRÍTICA: o Planner não executa nada — só produz um plano. A
execução é responsabilidade exclusiva do RuntimeEngine.

EXCEÇÃO DOCUMENTADA AO PADRÃO "todo Agent fala com o LLM via
tool.llm_call" (a mesma regra que agent.critic violava incorretamente
na v0.1.1 — ver app.agents): o Planner chama call_llm() diretamente.
Motivo, preservado da v0.1.1 porque continua válido: o output do
Planner não é uma Envelope de resultado — é um TaskGraph inteiro. Se
passasse por tool.llm_call, o dispatcher embrulharia a resposta dentro
de uma Envelope, adicionando uma indireção sem função real e um ponto
a mais onde o parse do plano poderia quebrar. Diferente do caso do
agent.critic (que retorna um resultado normal e não tinha motivo
nenhum para pular o dispatcher), esta exceção tem uma razão técnica
concreta e está documentada aqui de propósito — não é um bug escondido,
é uma decisão de arquitetura com trade-off explícito.

v2.0 — a correção que fecha o bug mais grave encontrado na análise
pré-reconstrução: `AVAILABLE_TARGETS` era uma lista Python mantida à
mão aqui neste arquivo, com 11 entradas — mas o dispatcher só sabia
rotear um subconjunto delas. Qualquer plano que usasse um dos 5 alvos
sem handler falhava garantido com UNKNOWN_TARGET. Agora
`available_targets_for_planner()` deriva a lista DIRETO do registry
do dispatcher (`app.dispatcher.available_targets()`), então as duas
listas não podem mais divergir — não existem mais duas listas.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.dispatcher import available_targets as dispatcher_available_targets
from app.llm_client import call_llm
from app.planner_schemas import TaskGraph, TaskNode, validate_dag
from app.schemas import Domain, Envelope, Permissions

logger = logging.getLogger("agent.planner")

# target_id que o dispatcher conhece mas que NUNCA deve ser oferecido
# como alvo de tarefa de plano — são primitivas internas que os
# próprios Agents usam para falar com o LLM, não passos de plano com
# significado próprio (um TaskNode "target: tool.llm_call" não tem um
# formato de resultado que o RuntimeEngine saiba resumir).
_INTERNAL_ONLY_TARGETS: frozenset[str] = frozenset({"tool.llm_call"})

# Exemplo de payload por target_id, usado para montar o system prompt
# dinamicamente. Pode ter MAIS entradas do que o que está registrado
# agora mesmo (targets de fases futuras) — só as que baterem com
# available_targets_for_planner() aparecem de fato no prompt.
_PAYLOAD_EXAMPLES: dict[str, str] = {
    "tool.calculator": '{"expression": "347 * 289"}  (ou "sqrt(2) + 2**10")',
    "agent.researcher": '{"objective": "descrição da pesquisa"}, "context": {"domain": "matematica"}',
    "agent.critic": '{"content": "texto ou resultado a revisar", "criteria": "o que verificar (opcional)"}',
    "skill.rag_search": '{"query": "texto da consulta em linguagem natural"}',
    "skill.rag_index": '{"documents": ["texto 1", "texto 2"]}',
    "tool.ocr_extract": '{"file_path": "/documents/arquivo.pdf", "language": "por"}',
    "tool.transcribe_audio": '{"file_path": "/audio/gravacao.wav", "language": "pt"}',
    "tool.chromadb_add": '{"documents": ["texto 1", "texto 2"], "ids": ["id1", "id2"]}',
}

_PLANNER_SYSTEM_PROMPT_TEMPLATE = """Você é o Planner do Agent OS. Sua função é EXCLUSIVA:
decompor um objetivo do usuário em um grafo de tarefas (TaskGraph) no formato JSON.

REGRAS ABSOLUTAS:
1. NUNCA execute código, cálculos, ou buscas — apenas DESCREVA as tarefas.
2. Cada tarefa deve ter um target_id válido do sistema.
3. Use APENAS os target_ids listados abaixo em "FERRAMENTAS DISPONÍVEIS" — esta lista reflete exatamente o que o sistema sabe executar agora.
4. O campo 'payload' de cada tarefa deve conter TODOS os parâmetros necessários, no formato exato dos exemplos.
5. Use 'depends_on' para expressar ordem — nunca assuma ordem implícita.
6. Se uma tarefa precisa do resultado de outra, use '{{{{task_id:result_key}}}}' no payload.
7. task_id deve ser único e sequencial: T1, T2, T3...
8. NUNCA invente campos que não estão nos exemplos.

FERRAMENTAS DISPONÍVEIS E FORMATO DE PAYLOAD OBRIGATÓRIO:
{tools_with_examples}

FORMATO DE SAÍDA (JSON puro, SEM markdown, SEM blocos de código):
{{
  "tasks": [
    {{
      "task_id": "T1",
      "description": "descrição legível do que esta tarefa faz",
      "target_id": "tool.calculator",
      "payload": {{"expression": "347 * 289"}},
      "context": {{"domain": "matematica"}},
      "depends_on": [],
      "permissions_level": "read_only",
      "estimated_complexity": "low"
    }}
  ]
}}"""


def available_targets_for_planner() -> list[str]:
    """
    A fonte de verdade do que o Planner pode oferecer ao LLM — deriva
    direto do dispatcher, nunca uma lista separada mantida à mão.
    Calculada a cada chamada (não congelada em import time) porque
    novos módulos podem se registrar no dispatcher depois deste
    módulo ser importado (é assim que agent.critic e agent.researcher
    já funcionam, por exemplo).
    """
    return sorted(dispatcher_available_targets() - _INTERNAL_ONLY_TARGETS)


def _build_system_prompt(tools: list[str]) -> str:
    lines = []
    for target_id in tools:
        example = _PAYLOAD_EXAMPLES.get(target_id, "{}  (sem exemplo documentado ainda)")
        lines.append(f'- {target_id}\n  "payload": {example}')
    return _PLANNER_SYSTEM_PROMPT_TEMPLATE.format(tools_with_examples="\n".join(lines))


# ---------------------------------------------------------------------------
# Normalização de permissions_level — o LLM (especialmente modelos 7B)
# às vezes gera valores fora do enum (ex: "read_write"). Mapeia para
# um valor válido, com fallback seguro em "read_only".
# ---------------------------------------------------------------------------

_PERMISSION_LEVEL_MAP: dict[str, str] = {
    "read_only": "read_only", "readonly": "read_only", "read": "read_only", "r": "read_only",
    "read_write": "execute_sandboxed", "readwrite": "execute_sandboxed", "write": "execute_sandboxed",
    "execute": "execute_sandboxed", "sandboxed": "execute_sandboxed", "sandbox": "execute_sandboxed",
    "execute_with_confirmation": "execute_with_confirmation", "confirm": "execute_with_confirmation",
    "confirmation": "execute_with_confirmation",
    "full_access": "full_access", "full": "full_access", "admin": "full_access", "unrestricted": "full_access",
}


def _normalize_permissions_level(level: str | None) -> str:
    if not level or not isinstance(level, str):
        return "read_only"
    normalized = level.strip().lower().replace("-", "_").replace(" ", "_")
    result = _PERMISSION_LEVEL_MAP.get(normalized, "read_only")
    if result != level:
        logger.warning("permissions_level normalizado: '%s' -> '%s'", level, result)
    return result


def _normalize_calculator_payload(payload: dict) -> dict:
    """
    Normaliza payload do tool.calculator quando o LLM gera formato
    errado. Formatos conhecidos de erro: {"operation": "...",
    "operands": [a, b]}, {"a": x, "b": y, "op": "*"}, {"math": "..."}.
    """
    if "expression" in payload and isinstance(payload["expression"], str):
        return payload

    expression = None

    if "operands" in payload and isinstance(payload["operands"], list):
        op = payload.get("operation", "*")
        ops = payload["operands"]
        op_map = {
            "multiplication": "*", "multiply": "*", "mult": "*", "times": "*",
            "addition": "+", "add": "+", "sum": "+",
            "subtraction": "-", "subtract": "-", "minus": "-",
            "division": "/", "divide": "/", "div": "/",
            "power": "**", "pow": "**", "exponent": "**",
        }
        operator = op_map.get(str(op).lower(), str(op))
        expression = (
            f"{ops[0]} {operator} {ops[1]}" if len(ops) == 2
            else f" {operator} ".join(str(x) for x in ops)
        )
    elif "a" in payload and "b" in payload:
        expression = f"{payload['a']} {payload.get('op', '*')} {payload['b']}"
    elif "math" in payload and isinstance(payload["math"], str):
        expression = payload["math"]
    elif "formula" in payload and isinstance(payload["formula"], str):
        expression = payload["formula"]
    elif "calculation" in payload and isinstance(payload["calculation"], str):
        expression = payload["calculation"]

    if expression:
        logger.warning("Payload tool.calculator normalizado: %s -> {'expression': '%s'}", payload, expression)
        return {"expression": expression}

    logger.error("Não foi possível normalizar payload tool.calculator: %s", payload)
    return payload


_PAYLOAD_NORMALIZERS: dict[str, Any] = {
    "tool.calculator": _normalize_calculator_payload,
}


def _normalize_payload(target_id: str, payload: dict) -> dict:
    normalizer = _PAYLOAD_NORMALIZERS.get(target_id)
    return normalizer(payload) if normalizer else payload


def _layer_for_target(target_id: str) -> str:
    """Mapeia target_id para a camada correta do contrato (Contrato_Interfaces.md)."""
    if target_id.startswith("agent."):
        return "agent"
    if target_id.startswith("skill."):
        return "skill"
    if target_id.startswith("tool."):
        return "tool"
    logger.warning("target_id '%s' não segue convenção de camada, assumindo 'tool'", target_id)
    return "tool"


# Operações de alto risco que SEMPRE exigem confirmação humana,
# independente do que o Planner sugerir — heurística de segurança que
# não depende do LLM ter lembrado de marcar isso sozinho.
_HIGH_RISK_TARGETS: frozenset[str] = frozenset({
    "tool.python_exec", "tool.filesystem_delete", "tool.git_push", "tool.docker_run",
})


def _needs_confirmation(target_id: str, payload: dict) -> bool:
    if target_id in _HIGH_RISK_TARGETS:
        return True
    if target_id == "skill.rag_index" and payload.get("domain") in {"courier", "eletronica"}:
        return True
    return False


def _extract_json_from_text(text: str) -> dict:
    """Extrai JSON válido de texto que pode conter markdown ou ruído
    ao redor (modelos 7B frequentemente envolvem JSON em ```json```
    mesmo quando instruídos a não fazer isso)."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = re.sub(r"\s*```", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    raise RuntimeError(f"Não foi possível extrair JSON válido do Planner. Resposta: {text[:500]}")


async def run_planner(
    objective: str,
    domain: Domain,
    available_tools: Optional[list[str]] = None,
    max_tasks: int = 20,
) -> TaskGraph:
    """Produz um TaskGraph a partir de um objetivo em linguagem natural."""
    tools = available_tools if available_tools is not None else available_targets_for_planner()
    root_trace_id = str(uuid.uuid4())

    messages = [
        {"role": "system", "content": _build_system_prompt(tools)},
        {"role": "user", "content": (
            f"Objetivo: {objective}\n"
            f"Domínio: {domain}\n"
            f"Máximo de tarefas: {max_tasks}\n\n"
            f"Gere o TaskGraph como JSON puro, sem markdown."
        )},
    ]

    logger.info("plan_id=%s iniciando planejamento para: %s", root_trace_id, objective[:80])

    raw_response = await call_llm(messages=messages, temperature=0.1, max_tokens=4000)
    content = raw_response["choices"][0]["message"]["content"]

    plan_data = _extract_json_from_text(content)

    if "tasks" not in plan_data:
        raise RuntimeError(f"Planner retornou JSON sem campo 'tasks'. Conteúdo: {content[:200]}")
    if not isinstance(plan_data["tasks"], list):
        raise RuntimeError(f"Planner retornou 'tasks' que não é array. Tipo: {type(plan_data['tasks'])}")

    tasks = []
    for i, task_data in enumerate(plan_data["tasks"][:max_tasks]):
        task_id = task_data.get("task_id", f"T{i + 1}")
        target_id = task_data.get("target_id", "")

        if target_id not in tools:
            logger.warning(
                "plan_id=%s task_id=%s target_id='%s' não está na lista de ferramentas disponíveis",
                root_trace_id, task_id, target_id,
            )

        raw_payload = task_data.get("payload", {})
        payload = _normalize_payload(target_id, raw_payload)
        if payload != raw_payload:
            logger.info("plan_id=%s task_id=%s payload normalizado para %s", root_trace_id, task_id, target_id)

        task_context = task_data.get("context", {})
        if "domain" not in task_context:
            task_context["domain"] = domain

        envelope = Envelope(
            trace_id=root_trace_id,
            parent_id=f"{root_trace_id}:{task_id}",
            layer_from="runtime",
            layer_to=_layer_for_target(target_id),
            target_id=target_id,
            payload=payload,
            context=task_context,
            permissions=Permissions(
                level=_normalize_permissions_level(task_data.get("permissions_level")),
                requires_human_confirmation=_needs_confirmation(target_id, payload),
            ),
        )

        tasks.append(TaskNode(
            task_id=task_id,
            sequence=i,
            envelope=envelope,
            description=task_data.get("description", ""),
            estimated_complexity=task_data.get("estimated_complexity", "medium"),
            depends_on=task_data.get("depends_on", []),
            max_retries=task_data.get("max_retries", 1),
            retry_delay_seconds=task_data.get("retry_delay_seconds", 5),
        ))

    try:
        validate_dag(tasks)
    except RuntimeError as exc:
        logger.error("plan_id=%s DAG inválido: %s", root_trace_id, exc)
        raise RuntimeError(f"Plano gerado pelo LLM contém ciclo ou dependência inválida: {exc}") from exc

    total_tokens = raw_response.get("usage", {}).get("total_tokens", 0)
    logger.info("plan_id=%s plano gerado: %d tarefas, %d tokens consumidos", root_trace_id, len(tasks), total_tokens)

    return TaskGraph(
        plan_id=root_trace_id,
        objective=objective,
        domain=domain,
        created_at=datetime.now(timezone.utc),
        planner_model=raw_response.get("model", "unknown"),
        planning_tokens_consumed=total_tokens,
        tasks=tasks,
    )
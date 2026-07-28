"""
Dispatcher: recebe uma Envelope, valida permissões, roteia para o
handler certo baseado em target_id, e devolve a Envelope de resposta.

Regra de segurança central (Contrato de Interfaces, seção 7 / Manifesto
Princípio 10): a validação de permissões acontece SEMPRE aqui, na
camada receptora — nunca confiando que quem chamou já validou por si.

v2.0 — mudança estrutural em relação ao v0.1.1: lá, o nível mínimo de
permissão (`_MIN_PERMISSION_LEVEL`) e o roteamento de fato (uma cadeia
de `if envelope.target_id == "...":`) eram DUAS estruturas separadas,
mantidas manualmente em sincronia. Isso nunca gerou um bug direto
nelas mesmas, mas o mesmo padrão — uma lista de alvos válidos vivendo
separada de onde os alvos são de fato tratados — foi exatamente a
causa raiz de um bug real: o Planner tinha uma lista própria
(`AVAILABLE_TARGETS`) com 5 alvos que o dispatcher não sabia rotear.

Aqui, `register_handler()` amarra target_id + permissão exigida +
função handler numa ÚNICA chamada, então as duas nunca podem divergir
uma da outra DENTRO deste arquivo. E como é a mesma função
(`register_handler`) que vai alimentar o manifesto de alvos disponíveis
para o Planner (fase futura), a sincronia entre "o que o Planner pode
escolher" e "o que o dispatcher sabe executar" passa a vir de uma
única fonte de verdade, em vez de duas listas mantidas à mão.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

from app.calculator_engine import CalculatorError, evaluate as calculate_expression
from app.llm_client import LLMTimeoutError, LLMUpstreamError, call_llm
from app.schemas import Envelope, ErrorCode, make_error, make_result, validate_domain_if_required

DispatchResult = tuple[Envelope, float, str]
HandlerFn = Callable[[Envelope, float], Awaitable[DispatchResult]]

# Fonte única de verdade: target_id -> (níveis de permissão aceitos, handler).
# Populado só via register_handler(), nunca editado diretamente.
_REGISTRY: dict[str, tuple[set[str], HandlerFn]] = {}


def register_handler(target_id: str, allowed_levels: set[str], handler: HandlerFn) -> None:
    """
    Registra um handler para um target_id, junto com os níveis de
    permissão aceitos — atomicamente, numa única entrada. É assim que
    se adiciona uma Tool/Skill/Agent nova roteável: uma chamada aqui,
    não duas estruturas para manter sincronizadas à mão.
    """
    _REGISTRY[target_id] = (allowed_levels, handler)


def available_targets() -> frozenset[str]:
    """Todo target_id que o dispatcher sabe rotear agora mesmo. Usado
    pelo Planner (fase futura) para nunca oferecer ao LLM um alvo que
    o dispatcher não conhece — a mesma fonte de verdade dos dois
    lados."""
    return frozenset(_REGISTRY.keys())


def _has_sufficient_permission(target_id: str, level: str) -> bool:
    entry = _REGISTRY.get(target_id)
    if entry is None:
        return True
    allowed_levels, _handler = entry
    return level in allowed_levels


async def dispatch(envelope: Envelope) -> DispatchResult:
    start = time.perf_counter()

    entry = _REGISTRY.get(envelope.target_id)

    # 1) Alvo conhecido?
    if entry is None:
        latency_ms = (time.perf_counter() - start) * 1000
        return (
            make_error(envelope, ErrorCode.UNKNOWN_TARGET, f"Nenhuma rota para '{envelope.target_id}'"),
            latency_ms,
            "error",
        )

    allowed_levels, handler = entry

    # 2) Permissão suficiente?
    if envelope.permissions.level not in allowed_levels:
        latency_ms = (time.perf_counter() - start) * 1000
        return (
            make_error(
                envelope,
                ErrorCode.PERMISSION_DENIED,
                f"'{envelope.target_id}' exige um dos níveis {sorted(allowed_levels)}, recebido '{envelope.permissions.level}'",
            ),
            latency_ms,
            "error",
        )

    # 2.5) Isolamento por domínio (ADR-0008)
    domain_error = validate_domain_if_required(envelope.target_id, envelope.context)
    if domain_error is not None:
        latency_ms = (time.perf_counter() - start) * 1000
        return (
            make_error(envelope, ErrorCode.INVALID_INPUT, domain_error),
            latency_ms,
            "error",
        )

    # 3) Roteamento — sempre para o handler registrado junto da permissão
    return await handler(envelope, start)


# =========================================================================
# tool.calculator e tool.llm_call — handlers desta fase. Os demais
# (tool.ocr_extract, tool.transcribe_audio, tool.chromadb_add,
# skill.rag_search) chegam nas próximas fases, cada um trazendo seu
# próprio register_handler() quando o módulo dele nascer. agent.critic
# e agent.researcher se auto-registram a partir de app.agents — ver lá.
# =========================================================================

async def _handle_calculator(envelope: Envelope, start: float) -> DispatchResult:
    expression = envelope.payload.get("expression")
    if not expression or not isinstance(expression, str):
        latency_ms = (time.perf_counter() - start) * 1000
        return (
            make_error(envelope, ErrorCode.INVALID_INPUT, "payload.expression é obrigatório e deve ser texto"),
            latency_ms,
            "error",
        )

    try:
        calc_result = await asyncio.to_thread(calculate_expression, expression)
    except CalculatorError as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return (
            make_error(envelope, ErrorCode.INVALID_INPUT, str(exc)),
            latency_ms,
            "error",
        )
    except ZeroDivisionError:
        latency_ms = (time.perf_counter() - start) * 1000
        return (
            make_error(envelope, ErrorCode.INVALID_INPUT, "Divisão por zero"),
            latency_ms,
            "error",
        )

    latency_ms = (time.perf_counter() - start) * 1000
    result = make_result(
        envelope,
        payload={"status": "success", "result": {"expression": expression, **calc_result}},
        meta={"engine": "python-ast", "latency_ms": round(latency_ms, 1)},
    )
    return result, latency_ms, "success"


register_handler(
    "tool.calculator",
    {"read_only", "execute_sandboxed", "execute_with_confirmation", "full_access"},
    _handle_calculator,
)


async def _handle_llm_call(envelope: Envelope, start: float) -> DispatchResult:
    payload = envelope.payload
    messages = payload.get("messages")
    if not messages:
        latency_ms = (time.perf_counter() - start) * 1000
        return (
            make_error(envelope, ErrorCode.INVALID_INPUT, "payload.messages é obrigatório"),
            latency_ms,
            "error",
        )

    try:
        raw_response = await call_llm(
            messages=messages,
            model=payload.get("model", "local-model"),
            temperature=payload.get("temperature", 0.3),
            tools=payload.get("tools"),
            tool_choice=payload.get("tool_choice", "auto"),
            max_tokens=payload.get("max_tokens"),
        )
    except LLMTimeoutError:
        latency_ms = (time.perf_counter() - start) * 1000
        return (
            make_error(envelope, ErrorCode.TOOL_TIMEOUT, "Motor de LLM não respondeu a tempo", recoverable=True),
            latency_ms,
            "error",
        )
    except LLMUpstreamError as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return (
            make_error(envelope, ErrorCode.UPSTREAM_UNAVAILABLE, str(exc), recoverable=True),
            latency_ms,
            "error",
        )

    latency_ms = (time.perf_counter() - start) * 1000
    result = make_result(
        envelope,
        payload={"status": "success", "result": raw_response},
        meta={
            "engine": "lm-studio",
            "latency_ms": round(latency_ms, 1),
            "tokens_input": raw_response.get("usage", {}).get("prompt_tokens"),
            "tokens_output": raw_response.get("usage", {}).get("completion_tokens"),
        },
    )
    return result, latency_ms, "success"


register_handler(
    "tool.llm_call",
    {"read_only", "execute_sandboxed", "execute_with_confirmation", "full_access"},
    _handle_llm_call,
)
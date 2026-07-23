"""
Implementação em Pydantic da Message Envelope definida em
01-ARCHITECTURE/Contrato_Interfaces.md (ADR-0001).

Este arquivo é o ponto de verdade do contrato em código — qualquer
mudança de formato de mensagem deve ser refletida aqui E no documento
técnico, nunca só em um dos dois.

v2.0: contrato preservado 1:1 do v0.1.1 (decisão registrada em
ADR-0013 — reconstruir a implementação, não a arquitetura). A
diferença aqui é que este arquivo nasce acompanhado de
tests/test_schemas.py desde o primeiro commit, o que não existia
na v0.1.1.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

__all__ = [
    "Layer",
    "EnvelopeType",
    "PermissionLevel",
    "Domain",
    "DOMAIN_REQUIRED_TARGETS",
    "ErrorCode",
    "Permissions",
    "Envelope",
    "validate_domain_if_required",
    "make_result",
    "make_error",
    "make_pending_confirmation",
]

Layer = Literal["runtime", "agent", "skill", "tool"]
EnvelopeType = Literal["request", "result", "error", "pending_confirmation"]
PermissionLevel = Literal[
    "read_only",
    "execute_sandboxed",
    "execute_with_confirmation",
    "full_access",
]

# Enum fechado de domínios de conhecimento isolados entre si (ADR-0008).
# Adicionar um domínio novo é uma decisão arquitetural — exige um ADR
# próprio, não uma edição livre. Ver ADR-0008 para o raciocínio
# completo de por que isso NÃO é uma string livre.
Domain = Literal["matematica", "courier", "eletronica"]
_VALID_DOMAINS: frozenset[str] = frozenset({"matematica", "courier", "eletronica"})

# target_id cujo campo context.domain é obrigatório (ADR-0008).
DOMAIN_REQUIRED_TARGETS: frozenset[str] = frozenset({"skill.rag_search", "tool.chromadb_add"})


class ErrorCode(str, Enum):
    """
    Lista fechada de códigos de erro — ver seção 6 do Contrato de
    Interfaces. NUNCA retornar um error_code fora desta lista; se um
    caso novo aparecer, ele deve ser adicionado aqui E documentado,
    não inventado ad-hoc numa resposta.
    """

    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    INVALID_INPUT = "INVALID_INPUT"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    MODEL_HALLUCINATION_SUSPECTED = "MODEL_HALLUCINATION_SUSPECTED"
    UNKNOWN_TARGET = "UNKNOWN_TARGET"              # target_id sem rota conhecida
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"  # ex: LM Studio fora do ar
    HUMAN_REJECTED = "HUMAN_REJECTED"              # humano recusou confirmar a operação
    CONFIRMATION_NOT_FOUND = "CONFIRMATION_NOT_FOUND"  # id de confirmação inválido/expirado


class Permissions(BaseModel):
    level: PermissionLevel = "read_only"
    allowed_tools: list[str] = Field(default_factory=list)
    requires_human_confirmation: bool = False
    sandbox: Optional[str] = None


class Envelope(BaseModel):
    trace_id: str
    parent_id: Optional[str] = None
    layer_from: Layer
    layer_to: Layer
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    type: EnvelopeType = "request"
    target_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    permissions: Permissions = Field(default_factory=Permissions)
    meta: dict[str, Any] = Field(default_factory=dict)


def validate_domain_if_required(target_id: str, context: dict[str, Any]) -> Optional[str]:
    """
    Retorna uma mensagem de erro se target_id exige um domínio válido
    em context.domain e ele estiver ausente ou inválido; None se está
    tudo certo ou se este target_id não exige isolamento por domínio
    (ADR-0008).

    Chamado pelo dispatcher ANTES de rotear, seguindo o princípio de
    "a camada receptora valida, nunca confia no chamador" — o mesmo
    já usado para permissions.level.
    """
    if target_id not in DOMAIN_REQUIRED_TARGETS:
        return None

    domain = context.get("domain")
    if domain is None:
        return f"'{target_id}' exige context.domain, nenhum valor foi informado"
    if domain not in _VALID_DOMAINS:
        return f"'{target_id}' recebeu context.domain='{domain}', valores válidos: {sorted(_VALID_DOMAINS)}"
    return None


def make_result(request: Envelope, payload: dict[str, Any], meta: Optional[dict] = None) -> Envelope:
    """Constrói a Envelope de resposta a partir da Envelope de pedido,
    invertendo layer_from/layer_to e propagando trace_id — exatamente
    como especificado na seção 6 do Contrato de Interfaces."""
    return Envelope(
        trace_id=request.trace_id,
        parent_id=request.trace_id,
        layer_from=request.layer_to,
        layer_to=request.layer_from,
        type="result",
        target_id=request.target_id,
        payload=payload,
        meta=meta or {},
    )


def make_error(
    request: Envelope,
    error_code: ErrorCode,
    message: str,
    recoverable: bool = False,
) -> Envelope:
    """Constrói a Envelope de erro no formato fechado do contrato."""
    return Envelope(
        trace_id=request.trace_id,
        parent_id=request.trace_id,
        layer_from=request.layer_to,
        layer_to=request.layer_from,
        type="error",
        target_id=request.target_id,
        payload={
            "status": "error",
            "error_code": error_code.value,
            "message": message,
            "recoverable": recoverable,
        },
    )


def make_pending_confirmation(request: Envelope, confirmation_id: str) -> Envelope:
    """
    Constrói a Envelope de "aguardando confirmação humana" (RFC-0001,
    item R-3). NÃO é um erro — é um terceiro estado legítimo,
    diferente de "deu certo" e "deu errado": o sistema entendeu o
    pedido, mas está pausado esperando um humano confirmar antes de
    executar de fato.
    """
    return Envelope(
        trace_id=request.trace_id,
        parent_id=request.trace_id,
        layer_from=request.layer_to,
        layer_to=request.layer_from,
        type="pending_confirmation",
        target_id=request.target_id,
        payload={
            "status": "pending_confirmation",
            "confirmation_id": confirmation_id,
            "message": "Esta operação exige confirmação humana antes de executar.",
        },
    )
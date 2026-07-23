"""
Testes do contrato de Envelope (schemas.py).

Objetivo destes testes: travar o comportamento do contrato descrito
em 01-ARCHITECTURE/Contrato_Interfaces.md em código executável, pra
qualquer mudança futura acidental no contrato quebrar o CI em vez de
só aparecer em produção.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import (
    DOMAIN_REQUIRED_TARGETS,
    Envelope,
    ErrorCode,
    Permissions,
    make_error,
    make_pending_confirmation,
    make_result,
    validate_domain_if_required,
)


def _base_request(**overrides) -> Envelope:
    """Envelope de pedido mínimo, usado como ponto de partida pelos testes."""
    defaults = dict(
        trace_id="trace-123",
        layer_from="runtime",
        layer_to="tool",
        target_id="tool.calculator",
    )
    defaults.update(overrides)
    return Envelope(**defaults)


# ---------------------------------------------------------------------------
# Envelope — defaults e validação básica
# ---------------------------------------------------------------------------

class TestEnvelopeDefaults:
    def test_type_defaults_to_request(self):
        env = _base_request()
        assert env.type == "request"

    def test_permissions_default_to_read_only_no_confirmation(self):
        env = _base_request()
        assert env.permissions.level == "read_only"
        assert env.permissions.requires_human_confirmation is False
        assert env.permissions.allowed_tools == []

    def test_payload_context_meta_default_to_empty_dict(self):
        env = _base_request()
        assert env.payload == {}
        assert env.context == {}
        assert env.meta == {}

    def test_parent_id_defaults_to_none(self):
        env = _base_request()
        assert env.parent_id is None

    def test_layer_from_rejects_invalid_value(self):
        with pytest.raises(ValidationError):
            _base_request(layer_from="not_a_real_layer")

    def test_target_id_is_required(self):
        with pytest.raises(ValidationError):
            Envelope(trace_id="t", layer_from="runtime", layer_to="tool")


# ---------------------------------------------------------------------------
# make_result
# ---------------------------------------------------------------------------

class TestMakeResult:
    def test_inverts_layers(self):
        req = _base_request(layer_from="runtime", layer_to="tool")
        res = make_result(req, payload={"value": 42})
        assert res.layer_from == "tool"
        assert res.layer_to == "runtime"

    def test_preserves_trace_id_and_sets_parent_id(self):
        req = _base_request(trace_id="trace-abc")
        res = make_result(req, payload={})
        assert res.trace_id == "trace-abc"
        assert res.parent_id == "trace-abc"

    def test_type_is_result(self):
        res = make_result(_base_request(), payload={})
        assert res.type == "result"

    def test_carries_target_id_and_payload_through(self):
        req = _base_request(target_id="tool.calculator")
        res = make_result(req, payload={"value": 7})
        assert res.target_id == "tool.calculator"
        assert res.payload == {"value": 7}

    def test_meta_defaults_to_empty_dict_when_omitted(self):
        res = make_result(_base_request(), payload={})
        assert res.meta == {}

    def test_meta_is_carried_when_provided(self):
        res = make_result(_base_request(), payload={}, meta={"latency_ms": 12})
        assert res.meta == {"latency_ms": 12}


# ---------------------------------------------------------------------------
# make_error
# ---------------------------------------------------------------------------

class TestMakeError:
    def test_defaults_to_not_recoverable(self):
        err = make_error(_base_request(), ErrorCode.UNKNOWN_TARGET, "sem rota")
        assert err.payload["recoverable"] is False

    def test_recoverable_true_when_explicitly_set(self):
        err = make_error(
            _base_request(), ErrorCode.UPSTREAM_UNAVAILABLE, "timeout", recoverable=True
        )
        assert err.payload["recoverable"] is True

    def test_type_is_error(self):
        err = make_error(_base_request(), ErrorCode.INVALID_INPUT, "x")
        assert err.type == "error"

    def test_payload_contains_error_code_and_message(self):
        err = make_error(_base_request(), ErrorCode.TOOL_TIMEOUT, "excedeu 30s")
        assert err.payload["status"] == "error"
        assert err.payload["error_code"] == "TOOL_TIMEOUT"
        assert err.payload["message"] == "excedeu 30s"

    def test_inverts_layers_like_make_result(self):
        req = _base_request(layer_from="runtime", layer_to="agent")
        err = make_error(req, ErrorCode.PERMISSION_DENIED, "negado")
        assert err.layer_from == "agent"
        assert err.layer_to == "runtime"


# ---------------------------------------------------------------------------
# make_pending_confirmation
# ---------------------------------------------------------------------------

class TestMakePendingConfirmation:
    def test_type_is_pending_confirmation_not_error(self):
        env = make_pending_confirmation(_base_request(), confirmation_id="conf-1")
        assert env.type == "pending_confirmation"
        assert env.type != "error"

    def test_payload_carries_confirmation_id(self):
        env = make_pending_confirmation(_base_request(), confirmation_id="conf-42")
        assert env.payload["confirmation_id"] == "conf-42"
        assert env.payload["status"] == "pending_confirmation"


# ---------------------------------------------------------------------------
# validate_domain_if_required (ADR-0008)
# ---------------------------------------------------------------------------

class TestValidateDomainIfRequired:
    def test_none_for_target_outside_domain_required_set(self):
        assert validate_domain_if_required("tool.calculator", {}) is None

    def test_error_message_when_domain_missing(self):
        msg = validate_domain_if_required("skill.rag_search", {})
        assert msg is not None
        assert "context.domain" in msg

    def test_error_message_when_domain_invalid(self):
        msg = validate_domain_if_required("skill.rag_search", {"domain": "financeiro"})
        assert msg is not None
        assert "financeiro" in msg

    @pytest.mark.parametrize("domain", ["matematica", "courier", "eletronica"])
    def test_none_for_each_valid_domain(self, domain):
        assert validate_domain_if_required("skill.rag_search", {"domain": domain}) is None

    def test_domain_required_targets_matches_known_set(self):
        # Trava a lista fechada do ADR-0008 — se alguém adicionar um
        # target novo aqui sem atualizar o ADR (ou vice-versa), este
        # teste é o alarme.
        assert DOMAIN_REQUIRED_TARGETS == frozenset({"skill.rag_search", "tool.chromadb_add"})
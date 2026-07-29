"""
Testes do dispatcher.py.

Os testes de "permissão negada" e "domínio obrigatório" usam um
handler falso registrado só durante o teste (via fixture
isolated_registry) — não precisam de nenhum motor real, porque o que
está sendo testado é a lógica genérica do dispatch() (permissão,
domínio, roteamento), não um handler específico. O handler real de
tool.calculator é testado à parte, ponta a ponta.
"""

from __future__ import annotations

import asyncio

import pytest

from app.dispatcher import available_targets, dispatch
from app.schemas import Envelope, ErrorCode, make_result


def _run(coro):
    return asyncio.run(coro)


def _request(target_id: str, **overrides) -> Envelope:
    defaults = dict(
        trace_id="trace-1",
        layer_from="runtime",
        layer_to="tool",
        target_id=target_id,
    )
    defaults.update(overrides)
    return Envelope(**defaults)


# ---------------------------------------------------------------------------
# Alvo desconhecido
# ---------------------------------------------------------------------------

class TestUnknownTarget:
    def test_returns_unknown_target_error(self):
        req = _request("tool.isso_nao_existe")
        response, _latency, log_level = _run(dispatch(req))
        assert response.type == "error"
        assert response.payload["error_code"] == ErrorCode.UNKNOWN_TARGET.value
        assert log_level == "error"

    def test_unknown_target_is_not_recoverable(self):
        # Retry não ajuda quando o problema é "essa rota não existe" —
        # recoverable=True aqui faria o RuntimeEngine ficar tentando
        # de novo à toa.
        req = _request("tool.isso_nao_existe")
        response, _latency, _log = _run(dispatch(req))
        assert response.payload["recoverable"] is False


# ---------------------------------------------------------------------------
# Permissão — usa um handler falso registrado só para este teste
# ---------------------------------------------------------------------------

class TestPermission:
    def test_denied_when_level_not_in_allowed_set(self, isolated_registry):
        async def _fake_handler(envelope, start):
            return make_result(envelope, payload={"status": "success"}), 0.0, "success"

        isolated_registry.register_handler("tool.fake_restricted", {"full_access"}, _fake_handler)

        req = _request("tool.fake_restricted", permissions={"level": "read_only"})
        response, _latency, _log = _run(dispatch(req))

        assert response.type == "error"
        assert response.payload["error_code"] == ErrorCode.PERMISSION_DENIED.value

    def test_allowed_when_level_matches(self, isolated_registry):
        async def _fake_handler(envelope, start):
            return make_result(envelope, payload={"status": "success"}), 0.0, "success"

        isolated_registry.register_handler("tool.fake_restricted", {"full_access"}, _fake_handler)

        req = _request("tool.fake_restricted", permissions={"level": "full_access"})
        response, _latency, _log = _run(dispatch(req))

        assert response.type == "result"


# ---------------------------------------------------------------------------
# Isolamento por domínio (ADR-0008) — via handler falso registrado
# sob um target_id que já está em DOMAIN_REQUIRED_TARGETS.
# ---------------------------------------------------------------------------

class TestDomainIsolation:
    def test_missing_domain_blocks_before_reaching_handler(self, isolated_registry):
        handler_was_called = False

        async def _fake_handler(envelope, start):
            nonlocal handler_was_called
            handler_was_called = True
            return make_result(envelope, payload={}), 0.0, "success"

        isolated_registry.register_handler(
            "skill.rag_search",
            {"read_only", "execute_sandboxed", "execute_with_confirmation", "full_access"},
            _fake_handler,
        )

        req = _request("skill.rag_search", context={})  # sem domain
        response, _latency, _log = _run(dispatch(req))

        assert response.type == "error"
        assert response.payload["error_code"] == ErrorCode.INVALID_INPUT.value
        assert handler_was_called is False

    def test_valid_domain_reaches_handler(self, isolated_registry):
        async def _fake_handler(envelope, start):
            return make_result(envelope, payload={"domain_received": envelope.context["domain"]}), 0.0, "success"

        isolated_registry.register_handler(
            "skill.rag_search",
            {"read_only", "execute_sandboxed", "execute_with_confirmation", "full_access"},
            _fake_handler,
        )

        req = _request("skill.rag_search", context={"domain": "matematica"})
        response, _latency, _log = _run(dispatch(req))

        assert response.type == "result"
        assert response.payload["domain_received"] == "matematica"


# ---------------------------------------------------------------------------
# available_targets()
# ---------------------------------------------------------------------------

class TestAvailableTargets:
    def test_includes_calculator(self):
        assert "tool.calculator" in available_targets()

    def test_does_not_leak_fake_handlers_registered_in_other_tests(self):
        # Se isolated_registry não estivesse restaurando o estado
        # depois de cada teste, este teste pegaria "lixo" dos testes
        # de permissão/domínio acima.
        assert "tool.fake_restricted" not in available_targets()


# ---------------------------------------------------------------------------
# tool.calculator — handler real, ponta a ponta
# ---------------------------------------------------------------------------

class TestCalculatorHandlerEndToEnd:
    def test_success(self):
        req = _request(
            "tool.calculator",
            payload={"expression": "2 + 2"},
            permissions={"level": "read_only"},
        )
        response, _latency, log_level = _run(dispatch(req))

        assert response.type == "result"
        assert response.payload["result"]["value"] == 4
        assert log_level == "success"

    def test_missing_expression_returns_invalid_input(self):
        req = _request("tool.calculator", payload={}, permissions={"level": "read_only"})
        response, _latency, _log = _run(dispatch(req))

        assert response.type == "error"
        assert response.payload["error_code"] == ErrorCode.INVALID_INPUT.value

    def test_invalid_expression_returns_invalid_input_not_a_crash(self):
        req = _request(
            "tool.calculator",
            payload={"expression": "__import__('os')"},
            permissions={"level": "read_only"},
        )
        response, _latency, _log = _run(dispatch(req))

        assert response.type == "error"
        assert response.payload["error_code"] == ErrorCode.INVALID_INPUT.value

    def test_division_by_zero_returns_structured_error_not_a_crash(self):
        req = _request(
            "tool.calculator",
            payload={"expression": "1 / 0"},
            permissions={"level": "read_only"},
        )
        response, _latency, _log = _run(dispatch(req))

        assert response.type == "error"
        assert response.payload["error_code"] == ErrorCode.INVALID_INPUT.value

    def test_bug_regression_pow_function_does_not_hang(self):
        # Regressão de ponta a ponta do bug #1: antes, isso não
        # levantava erro estruturado nenhum no dispatcher — só travava.
        req = _request(
            "tool.calculator",
            payload={"expression": "pow(9, 999999999999)"},
            permissions={"level": "read_only"},
        )
        response, _latency, _log = _run(dispatch(req))

        assert response.type == "error"
        assert response.payload["error_code"] == ErrorCode.INVALID_INPUT.value


# ---------------------------------------------------------------------------
# skill.rag_search — handler real, ponta a ponta (chromadb_search mockado
# no ponto de uso dentro de dispatcher.py, sem precisar de um servidor
# ChromaDB real — isso já é coberto à parte em test_chromadb_client.py)
# ---------------------------------------------------------------------------

class TestRagSearchHandlerEndToEnd:
    def test_success_returns_matches(self, monkeypatch):
        fake_matches = [{"id": "d1", "text": "texto", "metadata": {}, "distance": 0.1}]
        monkeypatch.setattr("app.dispatcher.chromadb_search", lambda domain, query, n: fake_matches)

        req = _request(
            "skill.rag_search",
            payload={"query": "pergunta"},
            context={"domain": "matematica"},
            permissions={"level": "read_only"},
        )
        response, _latency, log_level = _run(dispatch(req))

        assert response.type == "result"
        assert response.payload["result"]["matches"] == fake_matches
        assert log_level == "success"

    def test_missing_query_returns_invalid_input(self):
        req = _request(
            "skill.rag_search", payload={}, context={"domain": "matematica"},
            permissions={"level": "read_only"},
        )
        response, _latency, _log = _run(dispatch(req))
        assert response.payload["error_code"] == ErrorCode.INVALID_INPUT.value

    def test_missing_domain_blocked_before_handler_runs(self):
        # Cobertura end-to-end (handler real, não um fake) do mesmo
        # comportamento já testado com handler falso em TestDomainIsolation.
        req = _request(
            "skill.rag_search", payload={"query": "x"}, context={},
            permissions={"level": "read_only"},
        )
        response, _latency, _log = _run(dispatch(req))
        assert response.payload["error_code"] == ErrorCode.INVALID_INPUT.value

    def test_vectordb_error_is_recoverable(self, monkeypatch):
        from app.chromadb_client import VectorDBError

        def _raise(domain, query, n):
            raise VectorDBError("vector-db fora do ar")

        monkeypatch.setattr("app.dispatcher.chromadb_search", _raise)

        req = _request(
            "skill.rag_search", payload={"query": "x"}, context={"domain": "matematica"},
            permissions={"level": "read_only"},
        )
        response, _latency, _log = _run(dispatch(req))

        assert response.payload["error_code"] == ErrorCode.UPSTREAM_UNAVAILABLE.value
        assert response.payload["recoverable"] is True


# ---------------------------------------------------------------------------
# tool.chromadb_add — handler real, ponta a ponta
# ---------------------------------------------------------------------------

class TestChromadbAddHandlerEndToEnd:
    def test_success_returns_documents_added_count(self, monkeypatch):
        monkeypatch.setattr("app.dispatcher.add_documents", lambda domain, docs, ids, meta: len(docs))

        req = _request(
            "tool.chromadb_add",
            payload={"documents": ["a", "b"], "ids": ["1", "2"]},
            context={"domain": "matematica"},
            permissions={"level": "execute_sandboxed"},
        )
        response, _latency, log_level = _run(dispatch(req))

        assert response.type == "result"
        assert response.payload["result"]["documents_added"] == 2
        assert log_level == "success"

    def test_read_only_permission_is_denied(self):
        # tool.chromadb_add escreve dados — read_only não deveria bastar,
        # diferente de skill.rag_search (que só lê).
        req = _request(
            "tool.chromadb_add",
            payload={"documents": ["a"], "ids": ["1"]},
            context={"domain": "matematica"},
            permissions={"level": "read_only"},
        )
        response, _latency, _log = _run(dispatch(req))
        assert response.payload["error_code"] == ErrorCode.PERMISSION_DENIED.value

    def test_mismatched_documents_and_ids_length_returns_invalid_input(self):
        req = _request(
            "tool.chromadb_add",
            payload={"documents": ["a", "b"], "ids": ["1"]},
            context={"domain": "matematica"},
            permissions={"level": "execute_sandboxed"},
        )
        response, _latency, _log = _run(dispatch(req))
        assert response.payload["error_code"] == ErrorCode.INVALID_INPUT.value

    def test_missing_domain_blocked_before_handler_runs(self):
        req = _request(
            "tool.chromadb_add",
            payload={"documents": ["a"], "ids": ["1"]},
            context={},
            permissions={"level": "execute_sandboxed"},
        )
        response, _latency, _log = _run(dispatch(req))
        assert response.payload["error_code"] == ErrorCode.INVALID_INPUT.value
"""
Testes de app.agents.

Duas camadas de teste:
1. Unitários — monkeypatch em app.agents.dispatch, isolando a lógica
   de cada Agent sem precisar de LM Studio real.
2. Um teste de integração (TestEndToEndViaRealDispatch) que passa
   pelo dispatch() DE VERDADE, com target_id="agent.critic", só
   trocando app.llm_client.call_llm por um dublê na borda mais baixa
   possível — existe para confirmar que o registro no dispatcher (a
   correção arquitetural desta fase) está de fato funcionando de
   ponta a ponta, não só que a função isolada está correta.
"""

from __future__ import annotations

import json

import pytest

from app.agents import _handle_critic, _handle_researcher
from app.dispatcher import available_targets, dispatch
from app.schemas import Envelope, Permissions


def _envelope(target_id: str, payload: dict, context: dict | None = None) -> Envelope:
    return Envelope(
        trace_id="trace-1",
        layer_from="runtime",
        layer_to="agent",
        target_id=target_id,
        payload=payload,
        context=context or {},
        permissions=Permissions(level="execute_sandboxed"),
    )


def _llm_response(content: str, finish_reason: str = "stop", tool_calls=None) -> dict:
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "choices": [{"message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 20},
    }


class TestRegistryWiring:
    def test_agent_critic_is_registered(self):
        assert "agent.critic" in available_targets()

    def test_agent_researcher_is_registered(self):
        assert "agent.researcher" in available_targets()


@pytest.mark.asyncio
class TestHandleCritic:
    async def test_missing_content_returns_invalid_input(self):
        envelope = _envelope("agent.critic", payload={})
        response, _latency, status = await _handle_critic(envelope, start=0.0)
        assert status == "error"
        assert response.payload["error_code"] == "INVALID_INPUT"

    async def test_valid_json_response_is_parsed(self, monkeypatch):
        async def fake_dispatch(envelope: Envelope):
            content = json.dumps({"approval": True, "feedback": "bom", "issues": []})
            from app.schemas import make_result
            return make_result(envelope, payload={"result": _llm_response(content)}), 5.0, "success"

        monkeypatch.setattr("app.agents.dispatch", fake_dispatch)

        envelope = _envelope("agent.critic", payload={"content": "texto a revisar"})
        response, _latency, status = await _handle_critic(envelope, start=0.0)

        assert status == "success"
        assert response.payload["result"]["approval"] is True
        assert response.payload["result"]["feedback"] == "bom"

    async def test_non_json_response_falls_back_gracefully(self, monkeypatch):
        async def fake_dispatch(envelope: Envelope):
            from app.schemas import make_result
            return make_result(envelope, payload={"result": _llm_response("isso não é json")}), 5.0, "success"

        monkeypatch.setattr("app.agents.dispatch", fake_dispatch)

        envelope = _envelope("agent.critic", payload={"content": "texto a revisar"})
        response, _latency, status = await _handle_critic(envelope, start=0.0)

        assert status == "success"
        assert response.payload["result"]["approval"] is None
        assert response.payload["result"]["feedback"] == "isso não é json"
        assert "não foi JSON válido" in response.payload["result"]["issues"][0]

    async def test_llm_failure_returns_upstream_unavailable_recoverable(self, monkeypatch):
        async def fake_dispatch(envelope: Envelope):
            from app.schemas import ErrorCode, make_error
            return make_error(envelope, ErrorCode.UPSTREAM_UNAVAILABLE, "LM Studio fora do ar"), 5.0, "error"

        monkeypatch.setattr("app.agents.dispatch", fake_dispatch)

        envelope = _envelope("agent.critic", payload={"content": "texto"})
        response, _latency, status = await _handle_critic(envelope, start=0.0)

        assert status == "error"
        assert response.payload["error_code"] == "UPSTREAM_UNAVAILABLE"
        assert response.payload["recoverable"] is True

    async def test_default_criteria_used_when_not_provided(self, monkeypatch):
        captured = {}

        async def fake_dispatch(envelope: Envelope):
            captured["payload"] = envelope.payload
            from app.schemas import make_result
            content = json.dumps({"approval": True, "feedback": "ok", "issues": []})
            return make_result(envelope, payload={"result": _llm_response(content)}), 5.0, "success"

        monkeypatch.setattr("app.agents.dispatch", fake_dispatch)

        envelope = _envelope("agent.critic", payload={"content": "x"})
        await _handle_critic(envelope, start=0.0)

        user_message = captured["payload"]["messages"][1]["content"]
        assert "Verifique precisão, coerência e completude." in user_message


@pytest.mark.asyncio
class TestHandleResearcher:
    async def test_missing_objective_returns_invalid_input(self):
        envelope = _envelope("agent.researcher", payload={})
        response, _latency, status = await _handle_researcher(envelope, start=0.0)
        assert status == "error"
        assert response.payload["error_code"] == "INVALID_INPUT"

    async def test_direct_answer_without_tool_call(self, monkeypatch):
        async def fake_dispatch(envelope: Envelope):
            from app.schemas import make_result
            return (
                make_result(envelope, payload={"result": _llm_response("42 é a resposta")}),
                5.0, "success",
            )

        monkeypatch.setattr("app.agents.dispatch", fake_dispatch)

        envelope = _envelope("agent.researcher", payload={"objective": "quanto é a resposta?"})
        response, _latency, status = await _handle_researcher(envelope, start=0.0)

        assert status == "success"
        result = response.payload["result"]
        assert result["action"] == "direct"
        assert result["answer"] == "42 é a resposta"
        assert result["used_search"] is False

    async def test_calculator_tool_call_flow(self, monkeypatch):
        call_log: list[str] = []

        async def fake_dispatch(envelope: Envelope):
            from app.schemas import make_result
            call_log.append(envelope.target_id)

            if envelope.target_id == "tool.calculator":
                return (
                    make_result(envelope, payload={"result": {"value": 700, "expression": "347*2"}}),
                    2.0, "success",
                )

            # tool.llm_call
            if len(call_log) == 1:
                # primeira chamada: LLM decide usar a calculadora
                tool_call = {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "calculator", "arguments": json.dumps({"expression": "347*2"})},
                }
                return (
                    make_result(envelope, payload={"result": _llm_response(
                        None, finish_reason="tool_calls", tool_calls=[tool_call]
                    )}),
                    5.0, "success",
                )
            # segunda chamada: resposta final depois do resultado da tool
            return (
                make_result(envelope, payload={"result": _llm_response("O resultado é 700")}),
                5.0, "success",
            )

        monkeypatch.setattr("app.agents.dispatch", fake_dispatch)

        envelope = _envelope("agent.researcher", payload={"objective": "quanto é 347*2?"})
        response, _latency, status = await _handle_researcher(envelope, start=0.0)

        assert status == "success"
        result = response.payload["result"]
        assert result["action"] == "tool_call"
        assert result["calculation"]["value"] == 700
        assert result["answer"] == "O resultado é 700"
        assert call_log == ["tool.llm_call", "tool.calculator", "tool.llm_call"]

    async def test_rag_search_tool_call_flow(self, monkeypatch):
        call_log: list[str] = []

        async def fake_dispatch(envelope: Envelope):
            from app.schemas import make_result
            call_log.append(envelope.target_id)

            if envelope.target_id == "skill.rag_search":
                return (
                    make_result(envelope, payload={"result": {"matches": [
                        {"id": "doc1", "text": "conteúdo relevante", "metadata": {"source": "x"}}
                    ]}}),
                    3.0, "success",
                )

            if len(call_log) == 1:
                tool_call = {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "rag_search", "arguments": json.dumps({"query": "pergunta"})},
                }
                return (
                    make_result(envelope, payload={"result": _llm_response(
                        None, finish_reason="tool_calls", tool_calls=[tool_call]
                    )}),
                    5.0, "success",
                )
            return (
                make_result(envelope, payload={"result": _llm_response("Resposta com fontes")}),
                5.0, "success",
            )

        monkeypatch.setattr("app.agents.dispatch", fake_dispatch)

        envelope = _envelope(
            "agent.researcher", payload={"objective": "pesquisa X"}, context={"domain": "matematica"},
        )
        response, _latency, status = await _handle_researcher(envelope, start=0.0)

        assert status == "success"
        result = response.payload["result"]
        assert result["used_search"] is True
        assert result["sources"] == [{"id": "doc1", "metadata": {"source": "x"}}]


@pytest.mark.asyncio
class TestEndToEndViaRealDispatch:
    """Passa pelo dispatch() de verdade com target_id='agent.critic',
    só trocando call_llm no fundo — confirma que o registro no
    dispatcher (a correção desta fase) funciona de ponta a ponta."""

    async def test_agent_critic_routes_through_real_dispatcher(self, monkeypatch):
        async def fake_call_llm(**kwargs):
            content = json.dumps({"approval": False, "feedback": "faltou fonte", "issues": ["sem citação"]})
            return _llm_response(content)

        monkeypatch.setattr("app.dispatcher.call_llm", fake_call_llm)

        envelope = _envelope("agent.critic", payload={"content": "um texto qualquer"})
        response, _latency, status = await dispatch(envelope)

        assert status == "success"
        assert response.payload["result"]["approval"] is False
        assert response.payload["result"]["issues"] == ["sem citação"]

    async def test_agent_critic_denies_read_only_permission(self):
        envelope = Envelope(
            trace_id="t1", layer_from="runtime", layer_to="agent",
            target_id="agent.critic", payload={"content": "x"},
            permissions=Permissions(level="read_only"),
        )
        response, _latency, status = await dispatch(envelope)
        assert status == "error"
        assert response.payload["error_code"] == "PERMISSION_DENIED"
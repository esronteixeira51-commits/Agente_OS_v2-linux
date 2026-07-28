"""
Testes de app.planner_agent.

Import de app.agents no topo é proposital e necessário: os handlers
agent.critic/agent.researcher só existem no registry do dispatcher
depois que app.agents é importado (registro via efeito colateral de
import — mesmo padrão que main.py vai precisar seguir mais pra
frente, importando todo módulo que se auto-registra).
"""

from __future__ import annotations

import json

import pytest

from app import agents  # noqa: F401 — dispara os register_handler() de agent.critic/researcher
from app.planner_agent import (
    _extract_json_from_text,
    _layer_for_target,
    _needs_confirmation,
    _normalize_calculator_payload,
    _normalize_permissions_level,
    available_targets_for_planner,
    run_planner,
)


def _fake_llm_response(content: str, model: str = "test-model", total_tokens: int = 100) -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "model": model,
        "usage": {"total_tokens": total_tokens},
    }


class TestAvailableTargetsForPlanner:
    def test_excludes_tool_llm_call(self):
        assert "tool.llm_call" not in available_targets_for_planner()

    def test_includes_registered_agents_and_tools(self):
        targets = available_targets_for_planner()
        assert "agent.critic" in targets
        assert "agent.researcher" in targets
        assert "tool.calculator" in targets

    def test_reflects_dispatcher_registry_live_not_a_frozen_snapshot(self, monkeypatch):
        """Regressão do bug original: a lista NÃO pode ser uma cópia
        congelada em import time — precisa refletir o registry atual
        a cada chamada."""
        from app import dispatcher

        async def fake_handler(envelope, start):
            raise NotImplementedError

        dispatcher.register_handler("tool.fake_novo", {"read_only"}, fake_handler)
        try:
            assert "tool.fake_novo" in available_targets_for_planner()
        finally:
            del dispatcher._REGISTRY["tool.fake_novo"]


class TestNormalizePermissionsLevel:
    @pytest.mark.parametrize("raw,expected", [
        ("read_only", "read_only"),
        ("readonly", "read_only"),
        ("read_write", "execute_sandboxed"),
        ("write", "execute_sandboxed"),
        ("confirm", "execute_with_confirmation"),
        ("admin", "full_access"),
        ("full", "full_access"),
        ("algo-nunca-visto", "read_only"),
        (None, "read_only"),
        ("", "read_only"),
    ])
    def test_maps_known_and_unknown_variants(self, raw, expected):
        assert _normalize_permissions_level(raw) == expected


class TestNormalizeCalculatorPayload:
    def test_already_correct_format_passes_through(self):
        payload = {"expression": "2 + 2"}
        assert _normalize_calculator_payload(payload) == payload

    def test_operands_format_converted(self):
        payload = {"operation": "multiplication", "operands": [347, 289]}
        assert _normalize_calculator_payload(payload) == {"expression": "347 * 289"}

    def test_a_b_op_format_converted(self):
        payload = {"a": 10, "b": 3, "op": "-"}
        assert _normalize_calculator_payload(payload) == {"expression": "10 - 3"}

    def test_math_field_converted(self):
        payload = {"math": "sqrt(2)"}
        assert _normalize_calculator_payload(payload) == {"expression": "sqrt(2)"}

    def test_unrecognized_format_returns_unchanged(self):
        payload = {"formato_desconhecido": 123}
        assert _normalize_calculator_payload(payload) == payload


class TestLayerForTarget:
    @pytest.mark.parametrize("target_id,expected_layer", [
        ("agent.researcher", "agent"),
        ("skill.rag_search", "skill"),
        ("tool.calculator", "tool"),
        ("algo_sem_prefixo_conhecido", "tool"),
    ])
    def test_maps_prefix_to_layer(self, target_id, expected_layer):
        assert _layer_for_target(target_id) == expected_layer


class TestNeedsConfirmation:
    def test_high_risk_target_always_needs_confirmation(self):
        assert _needs_confirmation("tool.python_exec", {}) is True

    def test_low_risk_target_does_not_need_confirmation(self):
        assert _needs_confirmation("tool.calculator", {}) is False

    def test_rag_index_needs_confirmation_for_sensitive_domain(self):
        assert _needs_confirmation("skill.rag_index", {"domain": "courier"}) is True

    def test_rag_index_does_not_need_confirmation_for_open_domain(self):
        assert _needs_confirmation("skill.rag_index", {"domain": "matematica"}) is False


class TestExtractJsonFromText:
    def test_pure_json_parses_directly(self):
        assert _extract_json_from_text('{"tasks": []}') == {"tasks": []}

    def test_json_wrapped_in_markdown_fences(self):
        text = '```json\n{"tasks": []}\n```'
        assert _extract_json_from_text(text) == {"tasks": []}

    def test_json_wrapped_in_plain_fences_without_language_tag(self):
        text = '```\n{"tasks": []}\n```'
        assert _extract_json_from_text(text) == {"tasks": []}

    def test_json_embedded_in_surrounding_prose(self):
        text = 'Aqui está o plano:\n{"tasks": []}\nEspero que ajude!'
        assert _extract_json_from_text(text) == {"tasks": []}

    def test_completely_invalid_text_raises_runtime_error(self):
        with pytest.raises(RuntimeError):
            _extract_json_from_text("isso não tem json nenhum")


@pytest.mark.asyncio
class TestRunPlanner:
    async def test_builds_taskgraph_from_valid_response(self, monkeypatch):
        content = json.dumps({
            "tasks": [{
                "task_id": "T1",
                "description": "calcular algo",
                "target_id": "tool.calculator",
                "payload": {"expression": "2 + 2"},
                "context": {},
                "depends_on": [],
                "permissions_level": "read_only",
            }]
        })

        async def fake_call_llm(**kwargs):
            return _fake_llm_response(content)

        monkeypatch.setattr("app.planner_agent.call_llm", fake_call_llm)

        plan = await run_planner(objective="calcular 2+2", domain="matematica")

        assert plan.objective == "calcular 2+2"
        assert plan.domain == "matematica"
        assert len(plan.tasks) == 1
        assert plan.tasks[0].envelope.target_id == "tool.calculator"
        assert plan.tasks[0].envelope.payload == {"expression": "2 + 2"}

    async def test_defaults_domain_into_context_when_missing(self, monkeypatch):
        content = json.dumps({
            "tasks": [{
                "task_id": "T1", "description": "x", "target_id": "tool.calculator",
                "payload": {"expression": "1+1"}, "depends_on": [],
            }]
        })

        async def fake_call_llm(**kwargs):
            return _fake_llm_response(content)

        monkeypatch.setattr("app.planner_agent.call_llm", fake_call_llm)

        plan = await run_planner(objective="x", domain="courier")
        assert plan.tasks[0].envelope.context["domain"] == "courier"

    async def test_normalizes_malformed_calculator_payload_from_llm(self, monkeypatch):
        content = json.dumps({
            "tasks": [{
                "task_id": "T1", "description": "x", "target_id": "tool.calculator",
                "payload": {"a": 5, "b": 6, "op": "*"}, "depends_on": [],
            }]
        })

        async def fake_call_llm(**kwargs):
            return _fake_llm_response(content)

        monkeypatch.setattr("app.planner_agent.call_llm", fake_call_llm)

        plan = await run_planner(objective="x", domain="matematica")
        assert plan.tasks[0].envelope.payload == {"expression": "5 * 6"}

    async def test_raises_on_cyclic_plan(self, monkeypatch):
        content = json.dumps({
            "tasks": [
                {"task_id": "T1", "description": "a", "target_id": "tool.calculator",
                 "payload": {"expression": "1"}, "depends_on": ["T2"]},
                {"task_id": "T2", "description": "b", "target_id": "tool.calculator",
                 "payload": {"expression": "2"}, "depends_on": ["T1"]},
            ]
        })

        async def fake_call_llm(**kwargs):
            return _fake_llm_response(content)

        monkeypatch.setattr("app.planner_agent.call_llm", fake_call_llm)

        with pytest.raises(RuntimeError, match="ciclo"):
            await run_planner(objective="x", domain="matematica")

    async def test_missing_tasks_field_raises(self, monkeypatch):
        async def fake_call_llm(**kwargs):
            return _fake_llm_response(json.dumps({"sem_tasks_aqui": True}))

        monkeypatch.setattr("app.planner_agent.call_llm", fake_call_llm)

        with pytest.raises(RuntimeError, match="tasks"):
            await run_planner(objective="x", domain="matematica")

    async def test_target_not_in_available_tools_is_kept_but_logged(self, monkeypatch, caplog):
        """Preserva o comportamento original: um target_id fora da
        lista disponível não é removido do plano — só logado. A
        rejeição de verdade acontece no dispatch(), não aqui."""
        content = json.dumps({
            "tasks": [{
                "task_id": "T1", "description": "x", "target_id": "agent.programmer",
                "payload": {}, "depends_on": [],
            }]
        })

        async def fake_call_llm(**kwargs):
            return _fake_llm_response(content)

        monkeypatch.setattr("app.planner_agent.call_llm", fake_call_llm)

        plan = await run_planner(objective="x", domain="matematica")
        assert plan.tasks[0].envelope.target_id == "agent.programmer"

    async def test_system_prompt_only_lists_currently_available_targets(self, monkeypatch):
        captured = {}

        async def fake_call_llm(**kwargs):
            captured["messages"] = kwargs["messages"]
            content = json.dumps({
                "tasks": [{"task_id": "T1", "description": "x", "target_id": "tool.calculator",
                           "payload": {"expression": "1"}, "depends_on": []}]
            })
            return _fake_llm_response(content)

        monkeypatch.setattr("app.planner_agent.call_llm", fake_call_llm)

        await run_planner(objective="x", domain="matematica")

        system_prompt = captured["messages"][0]["content"]
        assert "tool.calculator" in system_prompt
        assert "agent.critic" in system_prompt
        # tool.llm_call é primitiva interna — nunca deve aparecer como
        # opção de target_id para o LLM planejador escolher.
        assert "tool.llm_call" not in system_prompt
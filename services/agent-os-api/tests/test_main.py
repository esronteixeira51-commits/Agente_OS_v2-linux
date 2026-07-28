"""
Testes de app.main — usa fastapi.testclient.TestClient (HTTP real
contra o app, sem subir um servidor de verdade) e a fixture test_db
(SQLite em memória) para os endpoints que tocam banco.

app.agents já é importado por app.main, então agent.critic/researcher
já estão registrados no dispatcher quando estes testes rodam.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.planner_schemas import TaskGraph, TaskNode, TaskStatus
from app.schemas import Envelope, Permissions
from datetime import datetime, timezone


@pytest.fixture
def client(test_db):
    with TestClient(app) as c:
        yield c


class TestHealth:
    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestDispatchEndpoint:
    def test_calculator_dispatch_succeeds_end_to_end(self, client):
        """Sem mock nenhum — passa pela rota HTTP real até o
        calculator_engine real. Só é possível porque tool.calculator
        não depende de LM Studio nem de nenhum serviço externo."""
        envelope = {
            "trace_id": "trace-http-1",
            "layer_from": "runtime",
            "layer_to": "tool",
            "target_id": "tool.calculator",
            "payload": {"expression": "6 * 7"},
            "permissions": {"level": "read_only"},
        }
        response = client.post("/v1/dispatch", json=envelope)
        assert response.status_code == 200
        body = response.json()
        assert body["type"] == "result"
        assert body["payload"]["result"]["value"] == 42

    def test_unknown_target_returns_400(self, client):
        envelope = {
            "trace_id": "trace-http-2",
            "layer_from": "runtime",
            "layer_to": "tool",
            "target_id": "tool.nao_existe",
            "payload": {},
            "permissions": {"level": "read_only"},
        }
        response = client.post("/v1/dispatch", json=envelope)
        assert response.status_code == 400
        assert response.json()["payload"]["error_code"] == "UNKNOWN_TARGET"

    def test_requires_confirmation_returns_202_and_creates_pending_row(self, client):
        envelope = {
            "trace_id": "trace-http-3",
            "layer_from": "runtime",
            "layer_to": "tool",
            "target_id": "tool.calculator",
            "payload": {"expression": "1 + 1"},
            "permissions": {"level": "read_only", "requires_human_confirmation": True},
        }
        response = client.post("/v1/dispatch", json=envelope)
        assert response.status_code == 202
        body = response.json()
        assert body["type"] == "pending_confirmation"
        assert "confirmation_id" in body["payload"]


class TestConfirmationFlow:
    def test_confirm_executes_original_envelope(self, client):
        envelope = {
            "trace_id": "trace-http-4",
            "layer_from": "runtime",
            "layer_to": "tool",
            "target_id": "tool.calculator",
            "payload": {"expression": "10 + 5"},
            "permissions": {"level": "read_only", "requires_human_confirmation": True},
        }
        pending_response = client.post("/v1/dispatch", json=envelope)
        confirmation_id = pending_response.json()["payload"]["confirmation_id"]

        list_response = client.get("/v1/pending")
        assert any(row["confirmation_id"] == confirmation_id for row in list_response.json())

        confirm_response = client.post(f"/v1/confirm/{confirmation_id}")
        assert confirm_response.status_code == 200
        assert confirm_response.json()["payload"]["result"]["value"] == 15

        # depois de confirmado, não deve mais aparecer como pendente
        list_after = client.get("/v1/pending")
        assert not any(row["confirmation_id"] == confirmation_id for row in list_after.json())

    def test_reject_returns_human_rejected(self, client):
        envelope = {
            "trace_id": "trace-http-5",
            "layer_from": "runtime",
            "layer_to": "tool",
            "target_id": "tool.calculator",
            "payload": {"expression": "1"},
            "permissions": {"level": "read_only", "requires_human_confirmation": True},
        }
        pending_response = client.post("/v1/dispatch", json=envelope)
        confirmation_id = pending_response.json()["payload"]["confirmation_id"]

        reject_response = client.post(f"/v1/reject/{confirmation_id}")
        assert reject_response.status_code == 200
        assert reject_response.json()["payload"]["error_code"] == "HUMAN_REJECTED"

    def test_confirm_nonexistent_id_returns_404(self, client):
        response = client.post("/v1/confirm/id-que-nao-existe")
        assert response.status_code == 404
        assert response.json()["error_code"] == "CONFIRMATION_NOT_FOUND"


class TestPlanEndpoint:
    def test_create_plan_review_mode_does_not_execute(self, client, monkeypatch):
        async def fake_run_planner(**kwargs):
            envelope = Envelope(
                trace_id="plan-1", layer_from="runtime", layer_to="tool",
                target_id="tool.calculator", payload={"expression": "1+1"},
                permissions=Permissions(level="read_only"),
            )
            task = TaskNode(task_id="T1", sequence=0, envelope=envelope, description="soma")
            return TaskGraph(
                plan_id="plan-1", objective=kwargs["objective"], domain=kwargs["domain"],
                created_at=datetime.now(timezone.utc), tasks=[task], max_parallel_tasks=2,
            )

        monkeypatch.setattr("app.main.run_planner", fake_run_planner)

        response = client.post("/v1/plan", json={"objective": "somar 1+1", "domain": "matematica", "execute": False})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "pending_approval"
        assert body["task_count"] == 1
        assert body["max_parallel_tasks"] == 2

    def test_create_plan_execute_mode_respects_max_parallel_tasks(self, client, monkeypatch):
        """Regressão do item_004: max_parallel_tasks era decorativo —
        o RuntimeEngine sempre rodava com max_parallel=1 hardcoded,
        não importa o que o plano dissesse. Este teste falha se esse
        bug voltar."""
        captured_max_parallel = {}

        async def fake_run_planner(**kwargs):
            envelope = Envelope(
                trace_id="plan-2", layer_from="runtime", layer_to="tool",
                target_id="tool.calculator", payload={"expression": "2+2"},
                permissions=Permissions(level="read_only"),
            )
            task = TaskNode(task_id="T1", sequence=0, envelope=envelope, description="soma")
            return TaskGraph(
                plan_id="plan-2", objective=kwargs["objective"], domain=kwargs["domain"],
                created_at=datetime.now(timezone.utc), tasks=[task], max_parallel_tasks=5,
            )

        monkeypatch.setattr("app.main.run_planner", fake_run_planner)

        real_runtime_engine_cls = __import__("app.runtime_engine", fromlist=["RuntimeEngine"]).RuntimeEngine

        class SpyRuntimeEngine(real_runtime_engine_cls):
            def __init__(self, max_parallel: int = 1):
                captured_max_parallel["value"] = max_parallel
                super().__init__(max_parallel=max_parallel)

        monkeypatch.setattr("app.main.RuntimeEngine", SpyRuntimeEngine)

        response = client.post("/v1/plan", json={"objective": "somar 2+2", "domain": "matematica", "execute": True})
        assert response.status_code == 200
        assert captured_max_parallel["value"] == 5
        assert response.json()["summary"]["success"] == 1

    def test_plan_execute_direct_with_serialized_plan(self, client):
        envelope = Envelope(
            trace_id="plan-3", layer_from="runtime", layer_to="tool",
            target_id="tool.calculator", payload={"expression": "3*3"},
            permissions=Permissions(level="read_only"),
        )
        task = TaskNode(task_id="T1", sequence=0, envelope=envelope, description="multiplicar")
        plan = TaskGraph(
            plan_id="plan-3", objective="multiplicar", domain="matematica",
            created_at=datetime.now(timezone.utc), tasks=[task],
        )

        response = client.post("/v1/plan/execute", json={"plan_json": plan.model_dump_json()})
        assert response.status_code == 200
        body = response.json()
        assert body["summary"]["success"] == 1
        assert body["tasks"][0]["result_summary"]["value"] == 9

    def test_plan_execute_invalid_json_returns_400(self, client):
        response = client.post("/v1/plan/execute", json={"plan_json": "isto não é json válido"})
        assert response.status_code == 400
        assert response.json()["error_code"] == "INVALID_PLAN"

    def test_plan_execute_cyclic_plan_returns_400(self, client):
        envelope = Envelope(
            trace_id="plan-4", layer_from="runtime", layer_to="tool",
            target_id="tool.calculator", payload={"expression": "1"},
            permissions=Permissions(level="read_only"),
        )
        t1 = TaskNode(task_id="T1", sequence=0, envelope=envelope, description="a", depends_on=["T2"])
        t2 = TaskNode(task_id="T2", sequence=1, envelope=envelope, description="b", depends_on=["T1"])

        # Monta o JSON manualmente pra não passar pelo validate_dag do
        # próprio TaskGraph na construção (queremos testar a validação
        # do endpoint, não a do construtor).
        plan_dict = {
            "plan_id": "plan-4", "objective": "x", "domain": "matematica",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "tasks": [json.loads(t1.model_dump_json()), json.loads(t2.model_dump_json())],
        }

        response = client.post("/v1/plan/execute", json={"plan_json": json.dumps(plan_dict)})
        assert response.status_code == 400
        assert response.json()["error_code"] == "INVALID_PLAN"


class TestAgentResearcherEndpoint:
    def test_direct_researcher_call(self, client, monkeypatch):
        async def fake_dispatch(envelope: Envelope):
            from app.schemas import make_result
            return (
                make_result(envelope, payload={"result": {
                    "choices": [{"message": {"content": "resposta direta"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }}),
                5.0, "success",
            )

        monkeypatch.setattr("app.agents.dispatch", fake_dispatch)

        response = client.post("/v1/agent/researcher", json={"objective": "pergunta simples", "domain": "matematica"})
        assert response.status_code == 200
        assert response.json()["answer"] == "resposta direta"
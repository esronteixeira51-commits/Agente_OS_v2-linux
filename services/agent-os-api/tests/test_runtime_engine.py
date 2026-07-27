"""
Testes do RuntimeEngine — dispatch() é substituído por um dublê
(monkeypatch) para controlar exatamente o que cada tarefa "recebe" do
dispatcher, sem depender de LM Studio, calculadora real, etc.

O teste mais importante deste arquivo é
`test_recoverable_error_is_retried_and_can_succeed`: ele existe
especificamente para nunca deixar o bug de retry da v0.1.1 voltar
(a tarefa marcada READY que nunca era reenfileirada).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.planner_schemas import TaskGraph, TaskNode, TaskStatus
from app.runtime_engine import RuntimeEngine
from app.schemas import Envelope, ErrorCode, make_error, make_result


def _envelope(target_id: str = "tool.calculator", payload: dict | None = None) -> Envelope:
    return Envelope(
        trace_id="trace-1", layer_from="runtime", layer_to="tool",
        target_id=target_id, payload=payload or {},
    )


def _node(task_id: str, depends_on: list[str] | None = None, **overrides) -> TaskNode:
    defaults = dict(
        task_id=task_id,
        sequence=0,
        envelope=_envelope(),
        description=f"tarefa {task_id}",
        depends_on=depends_on or [],
        retry_delay_seconds=0,  # testes não esperam de verdade, por padrão
    )
    defaults.update(overrides)
    return TaskNode(**defaults)


def _plan(tasks: list[TaskNode], max_parallel_tasks: int = 1) -> TaskGraph:
    return TaskGraph(
        plan_id="plan-1", objective="teste", domain="matematica",
        tasks=tasks, created_at=datetime.now(timezone.utc),
        max_parallel_tasks=max_parallel_tasks,
    )


@pytest.mark.asyncio
class TestRetryRequeue:
    async def test_recoverable_error_is_retried_and_can_succeed(self, monkeypatch):
        """O teste de regressão do bug principal: uma falha recuperável
        deve fazer a tarefa rodar de novo de verdade, não desaparecer."""
        call_count = {"n": 0}

        async def fake_dispatch(envelope: Envelope):
            call_count["n"] += 1
            if call_count["n"] == 1:
                err = make_error(envelope, ErrorCode.UPSTREAM_UNAVAILABLE, "timeout", recoverable=True)
                return err, 10.0, "error"
            res = make_result(envelope, payload={"result": {"value": 42}})
            return res, 10.0, "success"

        monkeypatch.setattr("app.runtime_engine.dispatch", fake_dispatch)

        task = _node("t1", max_retries=1)
        plan = _plan([task])

        engine = RuntimeEngine(max_parallel=1)
        result_plan = await engine.execute(plan)

        assert call_count["n"] == 2, "dispatch deveria ter sido chamado 2x (falha + retry)"
        assert result_plan.tasks[0].status == TaskStatus.SUCCESS
        assert result_plan.tasks[0].max_retries == 0

    async def test_retries_exhausted_ends_failed(self, monkeypatch):
        call_count = {"n": 0}

        async def always_recoverable_error(envelope: Envelope):
            call_count["n"] += 1
            err = make_error(envelope, ErrorCode.UPSTREAM_UNAVAILABLE, "timeout", recoverable=True)
            return err, 10.0, "error"

        monkeypatch.setattr("app.runtime_engine.dispatch", always_recoverable_error)

        task = _node("t1", max_retries=2)
        plan = _plan([task])

        engine = RuntimeEngine(max_parallel=1)
        result_plan = await engine.execute(plan)

        # 1 tentativa original + 2 retries = 3 chamadas
        assert call_count["n"] == 3
        assert result_plan.tasks[0].status == TaskStatus.FAILED
        assert result_plan.tasks[0].max_retries == 0
        assert len(result_plan.tasks[0].error_log) == 3

    async def test_non_recoverable_error_is_not_retried(self, monkeypatch):
        call_count = {"n": 0}

        async def non_recoverable_error(envelope: Envelope):
            call_count["n"] += 1
            err = make_error(envelope, ErrorCode.UNKNOWN_TARGET, "sem rota", recoverable=False)
            return err, 5.0, "error"

        monkeypatch.setattr("app.runtime_engine.dispatch", non_recoverable_error)

        task = _node("t1", max_retries=3)
        plan = _plan([task])

        engine = RuntimeEngine(max_parallel=1)
        result_plan = await engine.execute(plan)

        assert call_count["n"] == 1, "erro não-recuperável nunca deve gerar retry"
        assert result_plan.tasks[0].status == TaskStatus.FAILED
        assert result_plan.tasks[0].max_retries == 3, "max_retries não deve ser consumido sem retry"

    async def test_retry_delay_seconds_is_honored(self, monkeypatch):
        """Confirma que o campo retry_delay_seconds (antes decorativo)
        agora é de fato lido — usamos monkeypatch no asyncio.sleep pra
        não esperar de verdade no teste, só confirmar que foi chamado
        com o valor certo."""
        sleep_calls: list[float] = []

        async def fake_sleep(seconds):
            sleep_calls.append(seconds)

        monkeypatch.setattr("app.runtime_engine.asyncio.sleep", fake_sleep)

        call_count = {"n": 0}

        async def fake_dispatch(envelope: Envelope):
            call_count["n"] += 1
            if call_count["n"] == 1:
                err = make_error(envelope, ErrorCode.UPSTREAM_UNAVAILABLE, "timeout", recoverable=True)
                return err, 10.0, "error"
            return make_result(envelope, payload={"result": {"value": 1}}), 10.0, "success"

        monkeypatch.setattr("app.runtime_engine.dispatch", fake_dispatch)

        task = _node("t1", max_retries=1, retry_delay_seconds=7)
        plan = _plan([task])

        engine = RuntimeEngine(max_parallel=1)
        await engine.execute(plan)

        assert sleep_calls == [7]

    async def test_zero_retry_delay_does_not_call_sleep(self, monkeypatch):
        sleep_calls: list[float] = []
        monkeypatch.setattr(
            "app.runtime_engine.asyncio.sleep",
            lambda s: sleep_calls.append(s) or _instant_future(),
        )

        call_count = {"n": 0}

        async def fake_dispatch(envelope: Envelope):
            call_count["n"] += 1
            if call_count["n"] == 1:
                err = make_error(envelope, ErrorCode.UPSTREAM_UNAVAILABLE, "timeout", recoverable=True)
                return err, 10.0, "error"
            return make_result(envelope, payload={"result": {"value": 1}}), 10.0, "success"

        monkeypatch.setattr("app.runtime_engine.dispatch", fake_dispatch)

        task = _node("t1", max_retries=1, retry_delay_seconds=0)
        plan = _plan([task])

        engine = RuntimeEngine(max_parallel=1)
        await engine.execute(plan)

        assert sleep_calls == [], "retry_delay_seconds=0 não deveria chamar sleep"


def _instant_future():
    import asyncio
    fut = asyncio.get_event_loop().create_future()
    fut.set_result(None)
    return fut


@pytest.mark.asyncio
class TestDependencyGraph:
    async def test_success_unblocks_dependent_task(self, monkeypatch):
        seen_targets: list[str] = []

        async def fake_dispatch(envelope: Envelope):
            seen_targets.append(envelope.target_id)
            return make_result(envelope, payload={"result": {"value": 1}}), 5.0, "success"

        monkeypatch.setattr("app.runtime_engine.dispatch", fake_dispatch)

        t1 = _node("t1", envelope=_envelope("tool.calculator"))
        t2 = _node("t2", depends_on=["t1"], envelope=_envelope("tool.calculator"))
        plan = _plan([t1, t2])

        engine = RuntimeEngine(max_parallel=1)
        result_plan = await engine.execute(plan)

        by_id = {t.task_id: t for t in result_plan.tasks}
        assert by_id["t1"].status == TaskStatus.SUCCESS
        assert by_id["t2"].status == TaskStatus.SUCCESS
        assert len(seen_targets) == 2

    async def test_failed_task_blocks_dependent(self, monkeypatch):
        call_count = {"n": 0}

        async def fake_dispatch(envelope: Envelope):
            call_count["n"] += 1
            err = make_error(envelope, ErrorCode.UNKNOWN_TARGET, "sem rota", recoverable=False)
            return err, 5.0, "error"

        monkeypatch.setattr("app.runtime_engine.dispatch", fake_dispatch)

        t1 = _node("t1")
        t2 = _node("t2", depends_on=["t1"])
        plan = _plan([t1, t2])

        engine = RuntimeEngine(max_parallel=1)
        result_plan = await engine.execute(plan)

        by_id = {t.task_id: t for t in result_plan.tasks}
        assert by_id["t1"].status == TaskStatus.FAILED
        assert by_id["t2"].status == TaskStatus.BLOCKED
        assert call_count["n"] == 1, "t2 nunca deveria ter sido despachada"


@pytest.mark.asyncio
class TestPlaceholderResolution:
    async def test_placeholder_resolved_from_dependency_result(self, monkeypatch):
        received_payloads: list[dict] = []

        async def fake_dispatch(envelope: Envelope):
            received_payloads.append(envelope.payload)
            if envelope.target_id == "tool.calculator" and "expression" not in envelope.payload:
                return make_result(envelope, payload={"result": {"value": 10}}), 5.0, "success"
            return make_result(envelope, payload={"result": {"value": 99}}), 5.0, "success"

        monkeypatch.setattr("app.runtime_engine.dispatch", fake_dispatch)

        t1 = _node("t1", envelope=_envelope("tool.calculator", payload={}))
        # Nota: o path do placeholder navega direto no result_summary,
        # que para uma Tool tipo calculadora é {"value": ...} (sem um
        # wrapper "result" por fora — ver _summarize_result). O path
        # correto aqui é "value", não "result.value".
        t2 = _node(
            "t2",
            depends_on=["t1"],
            envelope=_envelope("tool.calculator", payload={"value": "{{t1:value}}"}),
        )
        plan = _plan([t1, t2])

        engine = RuntimeEngine(max_parallel=1)
        await engine.execute(plan)

        # O placeholder precisa ser o valor INTEIRO do campo (regex
        # ancorada com ^...$) — não faz interpolação dentro de uma
        # string maior, é substituição de valor inteiro por valor real.
        assert received_payloads[1]["value"] == 10

    async def test_placeholder_embedded_in_larger_string_is_not_resolved(self, monkeypatch):
        """Documenta o comportamento real (herdado da v0.1.1): o
        placeholder só é resolvido quando é o valor INTEIRO do campo.
        Embutido dentro de outro texto, fica como está — não é um
        motor de interpolação de string."""
        received_payloads: list[dict] = []

        async def fake_dispatch(envelope: Envelope):
            received_payloads.append(envelope.payload)
            if "expression" not in envelope.payload:
                return make_result(envelope, payload={"result": {"value": 10}}), 5.0, "success"
            return make_result(envelope, payload={"result": {"value": 99}}), 5.0, "success"

        monkeypatch.setattr("app.runtime_engine.dispatch", fake_dispatch)

        t1 = _node("t1", envelope=_envelope("tool.calculator", payload={}))
        t2 = _node(
            "t2",
            depends_on=["t1"],
            envelope=_envelope("tool.calculator", payload={"expression": "{{t1:result.value}} + 1"}),
        )
        plan = _plan([t1, t2])

        engine = RuntimeEngine(max_parallel=1)
        await engine.execute(plan)

        assert received_payloads[1]["expression"] == "{{t1:result.value}} + 1"
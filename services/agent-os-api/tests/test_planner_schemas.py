"""
Testes de app.planner_schemas — principalmente validate_dag(), que
detecta ciclos e dependências para task_id inexistente antes do
RuntimeEngine sequer começar a executar.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.planner_schemas import TaskGraph, TaskNode, TaskStatus, validate_dag
from app.schemas import Envelope


def _envelope(target_id: str = "tool.calculator") -> Envelope:
    return Envelope(
        trace_id="trace-1", layer_from="runtime", layer_to="tool", target_id=target_id,
    )


def _node(task_id: str, depends_on: list[str] | None = None, **overrides) -> TaskNode:
    defaults = dict(
        task_id=task_id,
        sequence=0,
        envelope=_envelope(),
        description=f"tarefa {task_id}",
        depends_on=depends_on or [],
    )
    defaults.update(overrides)
    return TaskNode(**defaults)


class TestTaskNodeDefaults:
    def test_status_defaults_to_pending(self):
        assert _node("t1").status == TaskStatus.PENDING

    def test_max_retries_defaults_to_1(self):
        assert _node("t1").max_retries == 1

    def test_retry_delay_seconds_defaults_to_5(self):
        assert _node("t1").retry_delay_seconds == 5

    def test_error_log_defaults_to_empty_list(self):
        assert _node("t1").error_log == []


class TestValidateDag:
    def test_valid_linear_chain_does_not_raise(self):
        tasks = [_node("t1"), _node("t2", depends_on=["t1"]), _node("t3", depends_on=["t2"])]
        validate_dag(tasks)  # não deve levantar

    def test_dependency_on_nonexistent_task_raises(self):
        tasks = [_node("t1", depends_on=["fantasma"])]
        with pytest.raises(RuntimeError, match="fantasma"):
            validate_dag(tasks)

    def test_direct_cycle_raises(self):
        tasks = [_node("t1", depends_on=["t2"]), _node("t2", depends_on=["t1"])]
        with pytest.raises(RuntimeError, match="[Cc]iclo"):
            validate_dag(tasks)

    def test_self_dependency_raises(self):
        tasks = [_node("t1", depends_on=["t1"])]
        with pytest.raises(RuntimeError, match="[Cc]iclo"):
            validate_dag(tasks)

    def test_indirect_cycle_raises(self):
        # t1 -> t2 -> t3 -> t1
        tasks = [
            _node("t1", depends_on=["t3"]),
            _node("t2", depends_on=["t1"]),
            _node("t3", depends_on=["t2"]),
        ]
        with pytest.raises(RuntimeError, match="[Cc]iclo"):
            validate_dag(tasks)

    def test_diamond_shape_is_valid(self):
        # t1 -> (t2, t3) -> t4
        tasks = [
            _node("t1"),
            _node("t2", depends_on=["t1"]),
            _node("t3", depends_on=["t1"]),
            _node("t4", depends_on=["t2", "t3"]),
        ]
        validate_dag(tasks)  # não deve levantar

    def test_empty_task_list_does_not_raise(self):
        validate_dag([])


class TestTaskGraph:
    def test_max_parallel_tasks_defaults_to_1(self):
        graph = TaskGraph(
            plan_id="p1", objective="obj", domain="matematica",
            tasks=[_node("t1")], created_at=datetime.now(timezone.utc),
        )
        assert graph.max_parallel_tasks == 1
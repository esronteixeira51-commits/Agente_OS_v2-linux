"""
Estruturas de dados do Planner e do Runtime Engine.

v2.0: porta quase 1:1 do v0.1.1 — nenhum bug foi encontrado nesta
estrutura específica durante a análise/PCM. O que muda é só
organização e comentários; TaskGraph, TaskNode, TaskStatus e
validate_dag mantêm o mesmo comportamento.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.schemas import Domain, Envelope


class TaskStatus(str, Enum):
    PENDING = "pending"    # Aguardando dependências
    READY = "ready"        # Dependências satisfeitas (ou retry agendado), pode executar
    RUNNING = "running"    # Em execução
    SUCCESS = "success"    # Concluído com sucesso
    FAILED = "failed"      # Erro terminal (não-recuperável, ou retries esgotados)
    BLOCKED = "blocked"    # Downstream de uma tarefa FAILED/BLOCKED


class TaskNode(BaseModel):
    """
    Um nó no grafo de tarefas. Contém tudo que o Runtime precisa para
    despachar esta tarefa sem perguntar mais nada ao Planner.
    """

    task_id: str
    sequence: int

    envelope: Envelope  # Reutiliza schemas.Envelope

    description: str
    estimated_complexity: Literal["low", "medium", "high"] = "medium"

    depends_on: list[str] = Field(default_factory=list)

    max_retries: int = 1
    retry_delay_seconds: int = 5

    status: TaskStatus = TaskStatus.PENDING
    result_summary: Optional[dict] = None
    error_log: list[dict] = Field(default_factory=list)

    model_config = {"validate_assignment": True}


class TaskGraph(BaseModel):
    """
    O plano completo. Imutável na estrutura (nós e arestas), mutável
    apenas no estado dos nós durante a execução.
    """

    plan_id: str
    objective: str
    domain: Domain  # Isolamento (ADR-0008)

    tasks: list[TaskNode]
    created_at: datetime
    max_parallel_tasks: int = 1

    planner_model: str = "unknown"
    planning_tokens_consumed: int = 0

    model_config = {"validate_assignment": True}


def validate_dag(tasks: list[TaskNode]) -> None:
    """
    Valida que o grafo de dependências não contém ciclos nem referências
    a task_id inexistente. Levanta RuntimeError se algum dos dois
    problemas for encontrado.
    """
    task_by_id = {t.task_id: t for t in tasks}

    for task in tasks:
        for dep in task.depends_on:
            if dep not in task_by_id:
                raise RuntimeError(
                    f"Tarefa '{task.task_id}' depende de '{dep}' que não existe no plano"
                )

    visited: set[str] = set()
    rec_stack: set[str] = set()

    def has_cycle(task_id: str) -> bool:
        visited.add(task_id)
        rec_stack.add(task_id)
        task = task_by_id[task_id]
        for dep in task.depends_on:
            if dep not in visited:
                if has_cycle(dep):
                    return True
            elif dep in rec_stack:
                return True
        rec_stack.remove(task_id)
        return False

    for task in tasks:
        if task.task_id not in visited:
            if has_cycle(task.task_id):
                raise RuntimeError(
                    f"Ciclo detectado no plano envolvendo tarefa '{task.task_id}'"
                )
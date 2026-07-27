"""
Runtime Engine — executa TaskGraphs respeitando dependências,
paralelismo, retry policy e resolução de placeholders entre tarefas.

Este é o "coração batendo" do Agent OS: ele não decide o que fazer,
apenas executa o que o Planner decidiu, na ordem correta.

v2.0 — dois bugs corrigidos em relação ao v0.1.1 (achados na análise
do código antes da reconstrução, ver ADR-0013 e histórico do PCM):

1. RETRY NÃO REENFILEIRAVA A TAREFA. `_execute_task` marcava
   `task.status = READY` num erro recuperável, mas nunca tinha acesso
   à `ready_queue` pra colocar a tarefa de volta nela — só
   `_process_completion` mexe na fila, e ela só olhava tarefas
   PENDING, nunca READY. Resultado: a tarefa "sumia" (nem sucesso, nem
   falha, nem executa de novo), e qualquer dependente dela ficava
   PENDING para sempre. Corrigido: `_process_completion` agora também
   trata o caso `status == READY` (retry agendado) e reenfileira.

2. `retry_delay_seconds` existia no TaskNode, o Planner até preenchia,
   mas o RuntimeEngine nunca lia esse campo — mesmo padrão do bug já
   conhecido do `max_parallel_tasks` decorativo, só que aqui. Agora
   `_execute_task` de fato espera `retry_delay_seconds` (via
   `asyncio.sleep`, não-bloqueante para as outras tarefas em paralelo)
   antes de reportar a tarefa como pronta para nova tentativa.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from datetime import datetime, timezone
from typing import Any

from app.dispatcher import dispatch
from app.planner_schemas import TaskGraph, TaskNode, TaskStatus, validate_dag
from app.schemas import Envelope

logger = logging.getLogger("runtime.engine")

_PLACEHOLDER_RE = re.compile(r"^\{\{(\w+):([\w.]+)\}\}$")


class RuntimeEngine:
    """
    Motor de execução de planos. Stateless — o estado vive no TaskGraph.

    Args:
        max_parallel: Número máximo de tarefas simultâneas.
    """

    def __init__(self, max_parallel: int = 1):
        self.max_parallel = max(max_parallel, 1)
        self._running: dict[str, asyncio.Task] = {}

    async def execute(self, plan: TaskGraph) -> TaskGraph:
        """Executa um plano completo até conclusão ou falha irrecuperável.
        Retorna o plano com todos os estados atualizados."""
        logger.info(
            "plan_id=%s iniciando execução com %d tarefas (max_parallel=%d)",
            plan.plan_id, len(plan.tasks), self.max_parallel,
        )

        # Re-valida DAG (defesa contra plano corrompido)
        validate_dag(plan.tasks)

        task_by_id = {t.task_id: t for t in plan.tasks}
        ready_queue: deque[TaskNode] = deque()

        # Inicializa: quem não tem dependências está pronto
        for task in plan.tasks:
            if not task.depends_on:
                task.status = TaskStatus.READY
                ready_queue.append(task)

        while ready_queue or self._running:
            while ready_queue and len(self._running) < self.max_parallel:
                task = ready_queue.popleft()
                self._running[task.task_id] = asyncio.create_task(
                    self._execute_task(task, plan, task_by_id)
                )

            if self._running:
                done, _ = await asyncio.wait(
                    self._running.values(), return_when=asyncio.FIRST_COMPLETED
                )
                for task_future in done:
                    finished_task_id = next(
                        tid for tid, tf in self._running.items() if tf == task_future
                    )
                    del self._running[finished_task_id]
                    finished_task = task_by_id[finished_task_id]
                    self._process_completion(finished_task, ready_queue, task_by_id)

        success_count = sum(1 for t in plan.tasks if t.status == TaskStatus.SUCCESS)
        failed_count = sum(1 for t in plan.tasks if t.status == TaskStatus.FAILED)
        blocked_count = sum(1 for t in plan.tasks if t.status == TaskStatus.BLOCKED)
        pending_count = sum(1 for t in plan.tasks if t.status == TaskStatus.PENDING)
        logger.info(
            "plan_id=%s concluído: %d sucesso, %d falha, %d bloqueado, %d pendente",
            plan.plan_id, success_count, failed_count, blocked_count, pending_count,
        )
        return plan

    async def _execute_task(self, task: TaskNode, plan: TaskGraph, task_by_id: dict) -> None:
        """Executa uma única tarefa via dispatcher. Ao final desta
        coroutine, task.status é sempre um estado terminal para esta
        rodada: SUCCESS, FAILED, ou READY (retry agendado — já esperou
        o delay, pronta para o próximo ciclo do loop principal)."""
        task.status = TaskStatus.RUNNING
        logger.info("plan_id=%s task_id=%s iniciando: %s", plan.plan_id, task.task_id, task.description)

        task.envelope.parent_id = f"{plan.plan_id}:{task.task_id}"
        resolved_envelope = self._resolve_placeholders(task.envelope, task_by_id)

        try:
            response, latency_ms, status = await dispatch(resolved_envelope)

            if status == "success":
                task.status = TaskStatus.SUCCESS
                task.result_summary = self._summarize_result(response)
                logger.info(
                    "plan_id=%s task_id=%s SUCESSO em %.1fms", plan.plan_id, task.task_id, latency_ms
                )
                return

            # status == "error"
            error_code = response.payload.get("error_code", "UNKNOWN")
            recoverable = response.payload.get("recoverable", False)
            error_msg = response.payload.get("message", "Erro desconhecido")

            task.error_log.append({
                "error_code": error_code,
                "message": error_msg,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            if recoverable and task.max_retries > 0:
                task.max_retries -= 1
                logger.warning(
                    "plan_id=%s task_id=%s falha recuperável (retry restante=%d), "
                    "aguardando %ds antes de tentar de novo: %s",
                    plan.plan_id, task.task_id, task.max_retries, task.retry_delay_seconds, error_msg,
                )
                if task.retry_delay_seconds > 0:
                    await asyncio.sleep(task.retry_delay_seconds)
                # READY sinaliza para _process_completion reenfileirar esta
                # tarefa — é isso que estava faltando na v0.1.1.
                task.status = TaskStatus.READY
            else:
                task.status = TaskStatus.FAILED
                logger.error("plan_id=%s task_id=%s FALHA: %s", plan.plan_id, task.task_id, error_msg)

        except Exception as exc:
            task.status = TaskStatus.FAILED
            task.error_log.append({
                "error_code": "RUNTIME_EXCEPTION",
                "message": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            logger.exception("plan_id=%s task_id=%s EXCEÇÃO: %s", plan.plan_id, task.task_id, exc)

    def _process_completion(
        self, finished_task: TaskNode, ready_queue: deque, task_by_id: dict[str, TaskNode],
    ) -> None:
        """Atualiza a fila e os dependentes quando uma tarefa termina uma
        rodada de execução."""

        # Retry agendado (BUG CORRIGIDO): a tarefa não terminou de
        # verdade, só está pronta para tentar de novo. Reenfileira e
        # para por aqui — não propaga nem libera dependentes ainda.
        if finished_task.status == TaskStatus.READY:
            ready_queue.append(finished_task)
            logger.info(
                "plan_id=%s task_id=%s reenfileirada para retry",
                finished_task.envelope.trace_id, finished_task.task_id,
            )
            return

        if finished_task.status in (TaskStatus.FAILED, TaskStatus.BLOCKED):
            self._block_downstream(finished_task, task_by_id)
            return

        # Sucesso: verifica quem ficou pronto
        for task in task_by_id.values():
            if task.status != TaskStatus.PENDING:
                continue
            deps_satisfied = all(
                task_by_id[dep].status == TaskStatus.SUCCESS for dep in task.depends_on
            )
            if deps_satisfied:
                task.status = TaskStatus.READY
                ready_queue.append(task)
                logger.info(
                    "plan_id=%s task_id=%s PRONTO (dependências satisfeitas)",
                    finished_task.envelope.trace_id, task.task_id,
                )

    def _block_downstream(self, failed_task: TaskNode, task_by_id: dict) -> None:
        """Propaga BLOCKED para todos os nós downstream de um nó falho."""
        to_block = [failed_task.task_id]
        blocked: set[str] = set()

        while to_block:
            current_id = to_block.pop()
            for task in task_by_id.values():
                if task.task_id in blocked:
                    continue
                if current_id in task.depends_on and task.status == TaskStatus.PENDING:
                    task.status = TaskStatus.BLOCKED
                    blocked.add(task.task_id)
                    to_block.append(task.task_id)
                    logger.warning(
                        "plan_id=%s task_id=%s BLOQUEADO por falha em %s",
                        failed_task.envelope.trace_id, task.task_id, current_id,
                    )

    def _resolve_placeholders(self, envelope: Envelope, task_by_id: dict) -> Envelope:
        """
        Substitui {{task_id:result.path}} no payload pelos valores reais
        dos resultados das tarefas anteriores.
        """

        def resolve_value(value: Any) -> Any:
            if not isinstance(value, str):
                return value

            match = _PLACEHOLDER_RE.match(value.strip())
            if not match:
                return value

            dep_task_id, result_path = match.groups()
            dep_task = task_by_id.get(dep_task_id)

            if not dep_task or dep_task.status != TaskStatus.SUCCESS:
                logger.warning(
                    "Placeholder não resolvido: %s (task %s não encontrada ou não-sucedida)",
                    value, dep_task_id,
                )
                return value

            if dep_task.result_summary is None:
                logger.warning("Placeholder não resolvido: %s (task %s sem result_summary)", value, dep_task_id)
                return value

            result: Any = dep_task.result_summary
            for key in result_path.split("."):
                if isinstance(result, dict):
                    result = result.get(key, value)
                elif isinstance(result, list) and key.isdigit():
                    idx = int(key)
                    if 0 <= idx < len(result):
                        result = result[idx]
                    else:
                        logger.warning("Índice %d fora do range em %s", idx, value)
                        return value
                else:
                    logger.warning("Caminho inválido em placeholder: %s", value)
                    return value

            return result

        def resolve_recursive(obj: Any) -> Any:
            if isinstance(obj, dict):
                return {k: resolve_recursive(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [resolve_recursive(v) for v in obj]
            return resolve_value(obj)

        new_payload = resolve_recursive(envelope.payload)
        return envelope.model_copy(update={"payload": new_payload})

    def _summarize_result(self, response) -> dict:
        """Extrai um resumo serializável do resultado para dependentes."""
        payload = response.payload
        result = payload.get("result", {})

        if isinstance(result, dict):
            if "content" in result:
                text = result["content"]
                return {
                    "text": text[:2000] if isinstance(text, str) else str(text)[:2000],
                    "has_tool_calls": result.get("has_tool_calls", False),
                    "finish_reason": result.get("finish_reason"),
                }
            if "value" in result:
                summary = {"value": result["value"]}
                for key in ["digit_count", "first_digits", "last_digits", "digit_sum"]:
                    if key in result:
                        summary[key] = result[key]
                return summary
            if "matches" in result:
                return {
                    "matches_count": len(result["matches"]),
                    "matches": [
                        {"id": m.get("id"), "text": m.get("text", "")[:500]}
                        for m in result["matches"][:5]
                    ],
                }
            if "text" in result:
                return {"text": result["text"][:2000]}
            if "documents_added" in result:
                return {"documents_added": result["documents_added"]}

        return {"raw": str(result)[:1000]}
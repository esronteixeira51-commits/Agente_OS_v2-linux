"""
agent-os-api — ponto de entrada único do Agent OS para dispatch de
Message Envelopes (01-ARCHITECTURE/Contrato_Interfaces.md, ADR-0001) e
execução de planos via Planner + Runtime Engine.

Endpoints principais:
  POST /v1/dispatch          — despacha uma Envelope individual
  POST /v1/plan               — cria (e opcionalmente executa) um plano
  POST /v1/plan/execute       — executa plano previamente serializado
  GET  /v1/pending            — lista operações aguardando aprovação
  POST /v1/confirm/{id}       — aprova operação pausada
  POST /v1/reject/{id}        — rejeita operação pausada
  POST /v1/agent/researcher   — agent.researcher direto (modo legado/fácil)

v2.0 — duas correções em relação ao v0.1.1 (ver ADR-0013):

1. `max_parallel_tasks` do TaskGraph era decorativo — o Planner
   preenchia, mas o RuntimeEngine sempre rodava com
   `max_parallel=1` hardcoded nos dois endpoints que criam um
   RuntimeEngine. Corrigido: `RuntimeEngine(max_parallel=plan.max_parallel_tasks or 1)`.

2. `AVAILABLE_TARGETS` não existe mais como constante importada de
   planner_agent — `run_planner()` já deriva a lista sozinho, direto
   do registry do dispatcher (ver app.planner_agent). Este arquivo só
   precisa IMPORTAR os módulos que se auto-registram
   (app.agents, e futuramente cada novo skill/tool) para que o
   registry esteja populado antes da API subir.

IMPORTANTE: a importação de `app.agents` abaixo não é decorativa —
mesmo sem chamar nada dela diretamente neste ponto do arquivo, é o
efeito colateral de import que registra agent.critic e
agent.researcher no dispatcher. Remover esse import faria os dois
"desaparecerem" silenciosamente do sistema, do mesmo jeito que
causou o bug original.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import agents  # noqa: F401 — registra agent.critic/agent.researcher no dispatcher
from app.agents import run_researcher
from app.db import PendingConfirmation, get_session, init_db
from app.dispatcher import dispatch
from app.logging_utils import log_execution
from app.planner_agent import run_planner
from app.planner_schemas import TaskGraph, TaskStatus, validate_dag
from app.runtime_engine import RuntimeEngine
from app.schemas import Domain, Envelope, ErrorCode, make_error, make_pending_confirmation

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agent-os-api")

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("agent-os-api v2.0.0 iniciado — banco de log pronto.")
    yield


app = FastAPI(title="Agent OS API", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "2.0.0"}


# =========================================================================
# Dispatch individual (modo direto, sem Planner)
# =========================================================================

@app.post("/v1/dispatch")
async def dispatch_envelope(envelope: Envelope) -> JSONResponse:
    """Despacha uma Envelope individual. Usado para chamadas diretas
    a Agents/Skills/Tools, ou quando o Runtime Engine executa tarefas
    de um plano."""
    if envelope.permissions.requires_human_confirmation:
        confirmation_id = str(uuid.uuid4())
        session = get_session()
        try:
            session.add(
                PendingConfirmation(
                    id=confirmation_id,
                    trace_id=envelope.trace_id,
                    target_id=envelope.target_id,
                    envelope_json=envelope.model_dump_json(),
                )
            )
            session.commit()
        finally:
            session.close()

        pending_envelope = make_pending_confirmation(envelope, confirmation_id)
        log_execution(envelope, "pending_confirmation", "pending", None, 0.0)

        logger.info(
            "trace_id=%s target=%s status=pending confirmation_id=%s",
            envelope.trace_id, envelope.target_id, confirmation_id,
        )
        return JSONResponse(content=pending_envelope.model_dump(mode="json"), status_code=202)

    response_envelope, latency_ms, status = await dispatch(envelope)
    log_execution(
        envelope,
        response_envelope.type,
        status,
        response_envelope.payload.get("error_code"),
        latency_ms,
        tokens_input=response_envelope.meta.get("tokens_input"),
        tokens_output=response_envelope.meta.get("tokens_output"),
    )

    logger.info(
        "trace_id=%s target=%s status=%s latency_ms=%.1f",
        envelope.trace_id, envelope.target_id, status, latency_ms,
    )

    http_status = 200 if status == "success" else 400
    return JSONResponse(content=response_envelope.model_dump(mode="json"), status_code=http_status)


# =========================================================================
# Confirmação humana
# =========================================================================

@app.get("/v1/pending")
def list_pending() -> JSONResponse:
    session = get_session()
    try:
        rows = (
            session.query(PendingConfirmation)
            .filter(PendingConfirmation.status == "pending")
            .order_by(PendingConfirmation.created_at.asc())
            .all()
        )
        result = [
            {
                "confirmation_id": row.id,
                "trace_id": row.trace_id,
                "target_id": row.target_id,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
    finally:
        session.close()
    return JSONResponse(content=result, status_code=200)


@app.post("/v1/confirm/{confirmation_id}")
async def confirm(confirmation_id: str) -> JSONResponse:
    session = get_session()
    try:
        row = session.get(PendingConfirmation, confirmation_id)
        if row is None or row.status != "pending":
            return JSONResponse(
                content={"status": "error", "error_code": ErrorCode.CONFIRMATION_NOT_FOUND.value},
                status_code=404,
            )

        original_envelope = Envelope.model_validate_json(row.envelope_json)
        original_envelope.permissions.requires_human_confirmation = False

        row.status = "approved"
        row.decided_at = datetime.now(timezone.utc)
        session.commit()
    finally:
        session.close()

    response_envelope, latency_ms, status = await dispatch(original_envelope)
    log_execution(
        original_envelope,
        response_envelope.type,
        status,
        response_envelope.payload.get("error_code"),
        latency_ms,
        tokens_input=response_envelope.meta.get("tokens_input"),
        tokens_output=response_envelope.meta.get("tokens_output"),
    )

    logger.info("confirmation_id=%s aprovado, trace_id=%s status=%s", confirmation_id, original_envelope.trace_id, status)

    http_status = 200 if status == "success" else 400
    return JSONResponse(content=response_envelope.model_dump(mode="json"), status_code=http_status)


@app.post("/v1/reject/{confirmation_id}")
def reject(confirmation_id: str) -> JSONResponse:
    session = get_session()
    try:
        row = session.get(PendingConfirmation, confirmation_id)
        if row is None or row.status != "pending":
            return JSONResponse(
                content={"status": "error", "error_code": ErrorCode.CONFIRMATION_NOT_FOUND.value},
                status_code=404,
            )

        original_envelope = Envelope.model_validate_json(row.envelope_json)

        row.status = "rejected"
        row.decided_at = datetime.now(timezone.utc)
        session.commit()
    finally:
        session.close()

    error_envelope = make_error(
        original_envelope, ErrorCode.HUMAN_REJECTED, "Operação rejeitada por decisão humana explícita.", recoverable=False,
    )
    log_execution(original_envelope, "error", "error", ErrorCode.HUMAN_REJECTED.value, 0.0)

    logger.info("confirmation_id=%s REJEITADO, trace_id=%s", confirmation_id, original_envelope.trace_id)

    return JSONResponse(content=error_envelope.model_dump(mode="json"), status_code=200)


# =========================================================================
# Agent layer — endpoint direto (modo legado/fácil)
#
# NOTA (item_005 do PCM original, ainda pendente de decisão sua): este
# endpoint hoje é redundante com POST /v1/dispatch usando
# target_id="agent.researcher" — desde a Fase 4, agent.researcher tem
# rota completa no dispatcher, o que não era o caso na v0.1.1 (era o
# bug mais grave encontrado). Mantido por enquanto como conveniência
# (uma chamada mais simples pra quem só quer perguntar algo direto,
# sem montar uma Envelope), mas é uma decisão de produto sua se vale
# depreciar em favor de só /v1/dispatch.
# =========================================================================

class AgentResearcherRequest(BaseModel):
    objective: str
    domain: Domain


@app.post("/v1/agent/researcher")
async def agent_researcher(request: AgentResearcherRequest) -> JSONResponse:
    try:
        result = await run_researcher(objective=request.objective, domain=request.domain)
    except RuntimeError as exc:
        logger.error("agent.researcher falhou: %s", exc)
        return JSONResponse(content={"status": "error", "message": str(exc)}, status_code=502)

    logger.info("agent.researcher trace_id=%s action=%s", result["trace_id"], result.get("action", "unknown"))
    return JSONResponse(content=result, status_code=200)


# =========================================================================
# Planner + Runtime Engine — execução orquestrada de planos
# =========================================================================

class PlannerRequest(BaseModel):
    objective: str
    domain: Domain
    execute: bool = False  # False = apenas retorna o plano para revisão
    max_tasks: int = 20


class ExecutePlanRequest(BaseModel):
    plan_json: str  # TaskGraph serializado (modo Fase 1: cliente guarda o plano)


def _serialize_plan(plan: TaskGraph) -> dict:
    return {
        "plan_id": plan.plan_id,
        "objective": plan.objective,
        "domain": plan.domain,
        "task_count": len(plan.tasks),
        "max_parallel_tasks": plan.max_parallel_tasks,
        "tasks": [
            {
                "task_id": t.task_id,
                "description": t.description,
                "target_id": t.envelope.target_id,
                "layer_to": t.envelope.layer_to,
                "depends_on": t.depends_on,
                "permissions_level": t.envelope.permissions.level,
                "requires_confirmation": t.envelope.permissions.requires_human_confirmation,
                "estimated_complexity": t.estimated_complexity,
                "payload": t.envelope.payload,
            }
            for t in plan.tasks
        ],
        "planning_metadata": {
            "planner_model": plan.planner_model,
            "planning_tokens_consumed": plan.planning_tokens_consumed,
            "created_at": plan.created_at.isoformat(),
        },
    }


def _serialize_execution_results(plan: TaskGraph) -> dict:
    return {
        "status": "completed",
        "plan_id": plan.plan_id,
        "summary": {
            "total": len(plan.tasks),
            "success": sum(1 for t in plan.tasks if t.status == TaskStatus.SUCCESS),
            "failed": sum(1 for t in plan.tasks if t.status == TaskStatus.FAILED),
            "blocked": sum(1 for t in plan.tasks if t.status == TaskStatus.BLOCKED),
            "pending": sum(1 for t in plan.tasks if t.status == TaskStatus.PENDING),
        },
        "tasks": [
            {
                "task_id": t.task_id,
                "status": t.status.value,
                "description": t.description,
                "target_id": t.envelope.target_id,
                "result_summary": t.result_summary,
                "error_count": len(t.error_log),
                "errors": t.error_log[-3:] if t.error_log else [],
            }
            for t in plan.tasks
        ],
    }


@app.post("/v1/plan")
async def create_plan(request: PlannerRequest) -> JSONResponse:
    """Modo revisão (execute=False): retorna o plano para inspeção
    humana. Modo execução (execute=True): executa imediatamente via
    RuntimeEngine, respeitando plan.max_parallel_tasks."""
    try:
        plan = await run_planner(
            objective=request.objective,
            domain=request.domain,
            max_tasks=request.max_tasks,
        )
    except RuntimeError as exc:
        logger.error("Planner falhou: %s", exc)
        return JSONResponse(
            content={"status": "error", "error_code": "PLANNER_FAILED", "message": str(exc)},
            status_code=502,
        )

    logger.info("plan_id=%s criado com %d tarefas (%d tokens)", plan.plan_id, len(plan.tasks), plan.planning_tokens_consumed)

    if not request.execute:
        return JSONResponse(content={"status": "pending_approval", **_serialize_plan(plan)}, status_code=200)

    # max_parallel_tasks lido de verdade agora (era hardcoded em 1 na v0.1.1)
    engine = RuntimeEngine(max_parallel=plan.max_parallel_tasks or 1)

    try:
        completed_plan = await engine.execute(plan)
    except RuntimeError as exc:
        logger.error("RuntimeEngine falhou: %s", exc)
        return JSONResponse(
            content={"status": "error", "error_code": "RUNTIME_FAILED", "message": str(exc)},
            status_code=502,
        )

    return JSONResponse(content=_serialize_execution_results(completed_plan), status_code=200)


@app.post("/v1/plan/execute")
async def execute_plan_direct(request: ExecutePlanRequest) -> JSONResponse:
    """Executa um plano previamente serializado (modo Fase 1: cliente
    guarda o JSON do plano e reenvia para execução)."""
    try:
        plan = TaskGraph.model_validate_json(request.plan_json)
    except Exception as exc:
        logger.error("Falha ao desserializar plano: %s", exc)
        return JSONResponse(
            content={"status": "error", "error_code": "INVALID_PLAN", "message": f"Plano inválido ou corrompido: {exc}"},
            status_code=400,
        )

    try:
        validate_dag(plan.tasks)
    except RuntimeError as exc:
        return JSONResponse(
            content={"status": "error", "error_code": "INVALID_PLAN", "message": str(exc)},
            status_code=400,
        )

    logger.info("plan_id=%s executando plano recebido por JSON", plan.plan_id)

    engine = RuntimeEngine(max_parallel=plan.max_parallel_tasks or 1)
    completed_plan = await engine.execute(plan)

    return JSONResponse(content=_serialize_execution_results(completed_plan), status_code=200)
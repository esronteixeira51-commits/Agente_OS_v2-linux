"""
Telemetria de execução — uma linha por Envelope processada, gravada
em ExecutionLog. Escrita síncrona de propósito: nesta fase o volume
é baixo o bastante para não justificar uma fila assíncrona, e log
síncrono nunca perde uma linha por causa de crash do processo.

v2.0: porta 1:1 do v0.1.1 — nenhum bug encontrado aqui na análise.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from app.db import ExecutionLog, get_session
from app.schemas import Envelope

logger = logging.getLogger("logging_utils")


def log_execution(
    envelope: Envelope,
    envelope_type: str,
    status: str,
    error_code: Optional[str],
    latency_ms: float,
    tokens_input: Optional[int] = None,
    tokens_output: Optional[int] = None,
) -> None:
    """Grava uma linha de ExecutionLog. Nunca levanta exceção para o
    chamador — uma falha de log não pode derrubar uma resposta que já
    foi processada com sucesso."""
    session = get_session()
    try:
        session.add(
            ExecutionLog(
                id=str(uuid.uuid4()),
                trace_id=envelope.trace_id,
                parent_id=envelope.parent_id,
                layer_from=envelope.layer_from,
                layer_to=envelope.layer_to,
                target_id=envelope.target_id,
                envelope_type=envelope_type,
                status=status,
                error_code=error_code,
                latency_ms=latency_ms,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
            )
        )
        session.commit()
    except Exception:
        logger.exception("Falha ao gravar ExecutionLog para trace_id=%s — resposta segue normalmente", envelope.trace_id)
        session.rollback()
    finally:
        session.close()
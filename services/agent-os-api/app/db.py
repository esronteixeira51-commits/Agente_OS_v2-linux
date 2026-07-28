"""
Persistência via SQLAlchemy sobre SQLite.

Escolha deliberada (ver 09-INFRASTRUCTURE/Preparacao_Ambiente_Linux.md):
usar o ORM desde o início, mesmo sendo "só" SQLite, para que uma
eventual migração para PostgreSQL seja uma troca de connection
string, não uma reescrita de queries.

v2.0 — na análise pré-reconstrução, `TaskGraphRecord` e
`PlanExecutionLog` foram encontradas como código morto: as classes
existiam no db.py da v0.1.1, mas nenhuma rota do main.py jamais
inseria ou consultava nelas — sobraram de um bloco de código colado
que dizia literalmente "ADIÇÕES a colar aqui" e nunca foi conectado a
nada. Não portadas. Só ExecutionLog e PendingConfirmation ficam, que
são as duas tabelas de fato usadas por log_execution() e pelo fluxo
de confirmação humana.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


class ExecutionLog(Base):
    """Uma linha por Envelope processada — trace_id já no formato
    certo para virar spans de tracing distribuído no futuro, sem
    migração de esquema."""

    __tablename__ = "execution_log"

    id = Column(String, primary_key=True)
    trace_id = Column(String, index=True, nullable=False)
    parent_id = Column(String, index=True, nullable=True)
    layer_from = Column(String, nullable=False)
    layer_to = Column(String, nullable=False)
    target_id = Column(String, index=True, nullable=False)
    envelope_type = Column(String, nullable=False)  # request | result | error | pending_confirmation
    status = Column(String, nullable=False)          # success | error | pending
    error_code = Column(String, nullable=True)
    latency_ms = Column(Float, nullable=True)
    tokens_input = Column(Integer, nullable=True)
    tokens_output = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class PendingConfirmation(Base):
    """Guarda a Envelope de request ORIGINAL, inteira, enquanto espera
    uma decisão humana. Fica em banco — não em memória — porque a
    aprovação pode demorar mais que a vida do processo."""

    __tablename__ = "pending_confirmation"

    id = Column(String, primary_key=True)  # confirmation_id, devolvido ao chamador
    trace_id = Column(String, index=True, nullable=False)
    target_id = Column(String, nullable=False)
    envelope_json = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="pending")  # pending | approved | rejected
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    decided_at = Column(DateTime, nullable=True)


# SQLite via arquivo, caminho vindo da configuração (montado como
# volume no docker-compose.yml, sobrevive a restart do container).
engine = create_engine(
    f"sqlite:///{settings.relational_db_path}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_session() -> Session:
    return SessionLocal()
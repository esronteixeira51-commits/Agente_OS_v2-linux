"""
Fixtures compartilhadas entre os testes do agent-os-api.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db as db_module
from app import dispatcher


@pytest.fixture
def isolated_registry():
    """
    Tira uma foto do _REGISTRY do dispatcher antes do teste e restaura
    depois — assim um teste que registra um handler falso (pra testar
    permissão ou isolamento de domínio sem precisar de um handler
    real) nunca vaza esse registro pros testes seguintes.
    """
    snapshot = dict(dispatcher._REGISTRY)
    yield dispatcher
    dispatcher._REGISTRY.clear()
    dispatcher._REGISTRY.update(snapshot)


@pytest.fixture
def test_db(monkeypatch, tmp_path):
    """
    Troca app.db.engine/SessionLocal por um SQLite em ARQUIVO
    temporário (não :memory:) isolado por teste.

    Por que arquivo e não :memory:: endpoints síncronos do FastAPI
    (ex: list_pending, reject) rodam em threads do threadpool do
    Starlette, uma por chamada — e SQLite usa SingletonThreadPool
    para :memory:, isolado POR THREAD. Isso faz uma chamada síncrona
    "ver" um banco :memory: vazio diferente do banco que uma chamada
    assíncrona (mesma thread do event loop) acabou de popular. Um
    arquivo real no disco não tem esse problema — todas as conexões
    apontam para o mesmo arquivo, thread nenhuma importa.
    """
    db_path = tmp_path / "test_agent_os.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    test_session_local = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    db_module.Base.metadata.create_all(bind=test_engine)

    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "SessionLocal", test_session_local)

    yield test_engine
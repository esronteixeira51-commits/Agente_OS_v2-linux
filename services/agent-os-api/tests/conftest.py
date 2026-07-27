"""
Fixtures compartilhadas entre os testes do agent-os-api.
"""

from __future__ import annotations

import pytest

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
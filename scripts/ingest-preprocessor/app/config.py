"""
Configuração do pré-processador de ingestão determinística.

Todo parâmetro aqui tem efeito reprodutível: mesma fonte + mesma
config = mesmos chunks/IDs sempre. Mudar um valor aqui é uma decisão
consciente de reindexação, não um efeito colateral acidental (ver
Ingestao_Base_Conhecimento_e_Notas.md, seção 9.2, item de
determinismo).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Domain = Literal["matematica", "courier", "eletronica"]
Mode = Literal["passthrough", "clean"]

VALID_DOMAINS: frozenset[str] = frozenset({"matematica", "courier", "eletronica"})


@dataclass(frozen=True)
class PipelineConfig:
    # Calibrado para modelos 7B/8B no hardware de referência (Ryzen 7
    # 5700G + RTX 5050 8GB + 32GB RAM) — ~700 tokens por chunk, overlap
    # de ~12% para não perder contexto na fronteira entre chunks.
    chunk_size_chars: int = 2800
    chunk_overlap_chars: int = 350

    # Proteção simples contra arquivo absurdamente grande passado por
    # engano (não é uma restrição de negócio, é sanidade de operação).
    max_file_size_bytes: int = 20 * 1024 * 1024  # 20 MB


DEFAULT_CONFIG = PipelineConfig()
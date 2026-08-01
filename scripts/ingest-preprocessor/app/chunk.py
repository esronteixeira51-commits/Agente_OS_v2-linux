"""
Etapa 4 do pipeline: chunk — fatia o Markdown em unidades indexáveis,
preferindo cortar nas fronteiras de heading (nunca no meio de uma
frase, se puder evitar). Blocos maiores que chunk_size_chars são
fatiados em janelas com overlap configurável.

Determinístico: mesma entrada + mesma config = exatamente os mesmos
chunks, sempre — é o que permite reindexação previsível.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")


@dataclass(frozen=True)
class Chunk:
    text: str
    heading_path: str
    chunk_index: int


def chunk_markdown(text: str, chunk_size_chars: int, chunk_overlap_chars: int) -> list[Chunk]:
    sections = _split_by_heading(text)

    chunks: list[Chunk] = []
    for heading_path, content in sections:
        content = content.strip()
        if not content:
            continue

        # Uma seção com só a linha de heading e nada de corpo (ex:
        # "# Vazio" seguido direto de outro heading) não é conteúdo
        # indexável — o heading_path da PRÓXIMA seção real já cobre
        # a hierarquia, então isso não perde informação nenhuma.
        content_lines = content.split("\n")
        if _HEADING_RE.match(content_lines[0]):
            body_only = "\n".join(content_lines[1:]).strip()
            if not body_only:
                continue

        if len(content) <= chunk_size_chars:
            chunks.append(Chunk(text=content, heading_path=heading_path, chunk_index=len(chunks)))
        else:
            for piece in _slide_window(content, chunk_size_chars, chunk_overlap_chars):
                chunks.append(Chunk(text=piece, heading_path=heading_path, chunk_index=len(chunks)))

    return chunks


def _split_by_heading(text: str) -> list[tuple[str, str]]:
    """Divide o texto em (heading_path, conteúdo) por seção, seguindo
    a hierarquia real de headings (# > ## > ### ...), não só o
    último heading visto — um novo H1 fecha qualquer H2/H3 aberto."""
    lines = text.split("\n")

    sections: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []  # (nível, título)
    current_lines: list[str] = []

    def flush():
        path = " > ".join(title for _, title in stack)
        sections.append((path, "\n".join(current_lines)))

    for line in lines:
        match = _HEADING_RE.match(line)
        if match:
            flush()
            current_lines = [line]

            level = len(match.group(1))
            title = match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        else:
            current_lines.append(line)

    flush()
    return sections


def _slide_window(content: str, size: int, overlap: int) -> list[str]:
    if overlap >= size:
        raise ValueError(f"chunk_overlap_chars ({overlap}) deve ser menor que chunk_size_chars ({size})")

    pieces: list[str] = []
    start = 0
    step = size - overlap

    while start < len(content):
        end = start + size
        pieces.append(content[start:end])
        if end >= len(content):
            break
        start += step

    return pieces
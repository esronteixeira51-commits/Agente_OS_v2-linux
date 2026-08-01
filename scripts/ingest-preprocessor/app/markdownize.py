"""
Etapa 3 do pipeline: markdownize — heurísticas determinísticas para
detectar estrutura (capítulos, seções numeradas, títulos curtos) e
converter em headings Markdown.

Só chamada no modo `clean` — no modo `passthrough`, o texto do
usuário já é Markdown organizado, e esta etapa é pulada inteira pelo
pipeline (mexer nele contrariaria o que passthrough promete).

Estas são heurísticas, não um parser perfeito — o objetivo é reduzir
ruído comum de texto exportado, não adivinhar estrutura arbitrária.
Quando o texto não bate com nenhum padrão reconhecido, ele passa
inalterado.
"""

from __future__ import annotations

import re

# "Capítulo 3", "Capítulo 3 - Derivadas", "CAPITULO IV" (com ou sem
# acento, maiúsculo ou não) -> H1.
_CHAPTER_RE = re.compile(
    r"^cap[ií]tulo\s+([\divxlcdm]+)\s*[-:.]?\s*(.*)$", re.IGNORECASE
)

# Seção numerada tipo "3.1 Definição", "3.1.2. Exemplos" -> nível de
# heading proporcional à profundidade da numeração (3 -> H1 já
# coberto acima como capítulo; 3.1 -> H2; 3.1.2 -> H3).
_NUMBERED_SECTION_RE = re.compile(r"^(\d+(?:\.\d+)+)\.?\s+(.+)$")

# Título curto isolado: linha com poucas palavras, sem pontuação
# final de frase (. ; ,), cercada por linha em branco antes E depois
# — candidato forte a heading que o pipeline de exportação "achatou"
# em texto simples.
_SHORT_HEADING_MAX_WORDS = 6
_SENTENCE_ENDING_PUNCTUATION = (".", ";", ",")


def markdownize(text: str) -> str:
    lines = text.split("\n")
    output_lines: list[str] = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        chapter_match = _CHAPTER_RE.match(stripped)
        if chapter_match:
            number, title = chapter_match.groups()
            heading = f"Capítulo {number}" + (f": {title}" if title else "")
            output_lines.append(f"# {heading}")
            continue

        section_match = _NUMBERED_SECTION_RE.match(stripped)
        if section_match:
            numbering, title = section_match.groups()
            depth = numbering.count(".") + 1  # "3.1" -> depth 2
            level = min(depth, 6)  # "3.1" (depth 2) -> "##" (nível 2)
            output_lines.append(f"{'#' * level} {numbering} {title}")
            continue

        if _looks_like_short_heading(stripped, lines, i):
            output_lines.append(f"## {stripped}")
            continue

        output_lines.append(line)

    return "\n".join(output_lines)


def _looks_like_short_heading(stripped: str, lines: list[str], index: int) -> bool:
    if not stripped or stripped.startswith("#"):
        return False
    if stripped.endswith(_SENTENCE_ENDING_PUNCTUATION):
        return False
    word_count = len(stripped.split())
    if word_count == 0 or word_count > _SHORT_HEADING_MAX_WORDS:
        return False

    prev_blank = index == 0 or lines[index - 1].strip() == ""
    next_blank = index == len(lines) - 1 or lines[index + 1].strip() == ""
    return prev_blank and next_blank
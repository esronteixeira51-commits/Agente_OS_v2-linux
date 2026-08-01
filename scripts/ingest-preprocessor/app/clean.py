"""
Etapa 2 do pipeline: clean — limpeza puramente determinística, sem
LLM. Nunca reescreve o significado do texto, só remove ruído
mecânico típico de exportação de PDF/OCR.

No modo passthrough, esta etapa é pulada por completo (o texto do
usuário já está organizado — mexer nele seria o oposto do que
passthrough promete).
"""

from __future__ import annotations

import re
import unicodedata

# Linha inteira sendo só um número (com espaço opcional ao redor) —
# típico resíduo de "número da página" que sobra ao extrair PDF.
_PAGE_NUMBER_LINE_RE = re.compile(r"^\s*\d{1,4}\s*$")

# Palavra quebrada por hífen no fim de uma linha, continuando na
# linha seguinte: "exemplo-\nplo" -> "exemplo-plo" (o hífen de
# quebra de linha é removido, palavra rejuntada).
_HYPHEN_LINEBREAK_RE = re.compile(r"(\w+)-\n(\w+)")

# 3+ linhas em branco seguidas viram exatamente 2 (separador de
# parágrafo), nunca mais que isso.
_EXCESS_BLANK_LINES_RE = re.compile(r"\n{3,}")


def clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    text = _HYPHEN_LINEBREAK_RE.sub(r"\1\2", text)

    lines = [
        line for line in text.split("\n")
        if not _PAGE_NUMBER_LINE_RE.match(line)
    ]
    text = "\n".join(lines)

    text = _EXCESS_BLANK_LINES_RE.sub("\n\n", text)

    return text.strip()
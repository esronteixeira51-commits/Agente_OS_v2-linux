"""
Etapa 1 do pipeline: extract — lê a fonte e devolve texto bruto, sem
nenhuma transformação de conteúdo. Só decodifica; não limpa, não
reformata.

PDF só-imagem (sem camada de texto) não é suportado aqui de propósito
(ver Ingestao_Base_Conhecimento_e_Notas.md, seção 2.3 — "fora de
escopo": OCR de PDF escaneado é o ocr-worker, uma fase futura
separada). Levanta um erro claro orientando a pessoa pro caminho
certo, em vez de silenciosamente devolver texto vazio.
"""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf"}


class ExtractError(Exception):
    """Levantado quando o arquivo não pode ser lido ou extraído."""


def extract(file_path: str, max_file_size_bytes: int) -> str:
    path = Path(file_path)

    if not path.exists():
        raise ExtractError(f"Arquivo não encontrado: {file_path}")

    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ExtractError(
            f"Extensão não suportada: '{path.suffix}'. "
            f"Suportadas: {sorted(SUPPORTED_EXTENSIONS)}"
        )

    size = path.stat().st_size
    if size > max_file_size_bytes:
        raise ExtractError(
            f"Arquivo tem {size} bytes, acima do limite de {max_file_size_bytes} bytes"
        )
    if size == 0:
        raise ExtractError(f"Arquivo vazio: {file_path}")

    if path.suffix.lower() == ".pdf":
        return _extract_pdf(path)
    return _extract_plain_text(path)


def _extract_plain_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ExtractError(
            f"Falha ao decodificar '{path}' como UTF-8: {exc}. "
            "Converta o arquivo para UTF-8 antes de ingerir."
        ) from exc


def _extract_pdf(path: Path) -> str:
    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise ExtractError(f"Falha ao abrir PDF '{path}': {exc}") from exc

    pages_text = [page.extract_text() or "" for page in reader.pages]
    full_text = "\n\n".join(pages_text).strip()

    if not full_text:
        raise ExtractError(
            f"'{path}' não tem camada de texto extraível (provável PDF escaneado/só-imagem). "
            "Este pré-processador não faz OCR — use o ocr-worker para extrair texto de "
            "PDFs escaneados antes de ingerir aqui."
        )

    return full_text
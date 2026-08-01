"""
Orquestra o pipeline completo: extract -> clean -> markdownize ->
chunk -> payload, e escreve os artefatos de saída em disco.

No modo `passthrough`, clean e markdownize são pulados por completo
— o texto do usuário é tratado como já organizado, mexer nele
contrariaria a promessa do modo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.chunk import chunk_markdown
from app.clean import clean
from app.config import VALID_DOMAINS, PipelineConfig
from app.extract import extract
from app.markdownize import markdownize


class PipelineError(Exception):
    """Erro de validação ou execução do pipeline, antes de chegar às
    etapas individuais (que têm suas próprias exceções)."""


@dataclass(frozen=True)
class PipelineResult:
    source: str
    domain: str
    mode: str
    chunk_count: int
    total_chars: int
    markdown_path: str
    chunks_dir: str
    payload_path: str
    manifest_path: str
    chromadb_payload: dict = field(repr=False)


def run_pipeline(
    input_path: str,
    source: str,
    domain: str,
    mode: str,
    output_dir: str,
    config: PipelineConfig = PipelineConfig(),
) -> PipelineResult:
    if domain not in VALID_DOMAINS:
        raise PipelineError(f"domain '{domain}' inválido. Válidos: {sorted(VALID_DOMAINS)}")
    if mode not in ("passthrough", "clean"):
        raise PipelineError(f"mode '{mode}' inválido. Válidos: passthrough, clean")
    if not source or not source.strip():
        raise PipelineError("source não pode ser vazio — é usado como slug estável nos IDs")

    raw_text = extract(input_path, config.max_file_size_bytes)

    if mode == "clean":
        text = markdownize(clean(raw_text))
    else:  # passthrough
        text = raw_text

    chunks = chunk_markdown(text, config.chunk_size_chars, config.chunk_overlap_chars)
    if not chunks:
        raise PipelineError(f"Nenhum conteúdo para indexar em '{input_path}' após o pipeline")

    documents = [c.text for c in chunks]
    ids = [f"{source}_chunk{c.chunk_index:04d}" for c in chunks]
    metadatas = [
        {
            "source": source,
            "domain": domain,
            "chunk_index": c.chunk_index,
            "heading_path": c.heading_path,
            "format": "markdown",
        }
        for c in chunks
    ]
    chromadb_payload = {"documents": documents, "ids": ids, "metadatas": metadatas}

    out = Path(output_dir)
    markdown_dir = out / "markdown"
    chunks_dir = out / "chunks"
    markdown_dir.mkdir(parents=True, exist_ok=True)
    chunks_dir.mkdir(parents=True, exist_ok=True)

    markdown_path = markdown_dir / f"{source}.md"
    markdown_path.write_text(text, encoding="utf-8")

    for chunk_id, doc_text in zip(ids, documents):
        (chunks_dir / f"{chunk_id}.md").write_text(doc_text, encoding="utf-8")

    payload_path = out / "chromadb_payload.json"
    payload_path.write_text(json.dumps(chromadb_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest = {
        "source": source,
        "domain": domain,
        "mode": mode,
        "input_path": str(Path(input_path).resolve()),
        "chunk_count": len(chunks),
        "total_chars": len(text),
        "config": {
            "chunk_size_chars": config.chunk_size_chars,
            "chunk_overlap_chars": config.chunk_overlap_chars,
        },
    }
    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return PipelineResult(
        source=source,
        domain=domain,
        mode=mode,
        chunk_count=len(chunks),
        total_chars=len(text),
        markdown_path=str(markdown_path),
        chunks_dir=str(chunks_dir),
        payload_path=str(payload_path),
        manifest_path=str(manifest_path),
        chromadb_payload=chromadb_payload,
    )
"""
CLI do pré-processador de ingestão.

Uso:
    python -m app.cli --mode passthrough -i artigo.md -s meu-artigo -d matematica -o ./out
    python -m app.cli --mode clean -i capitulo.txt -s livro-cap3 -d matematica -o ./out
"""

from __future__ import annotations

import argparse
import sys

from app.config import DEFAULT_CONFIG, PipelineConfig
from app.extract import ExtractError
from app.pipeline import PipelineError, run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ingest-preprocessor",
        description="Pré-processador determinístico de ingestão (extract -> clean -> markdownize -> chunk -> payload)",
    )
    parser.add_argument("--mode", choices=["passthrough", "clean"], required=True, help="passthrough: preserva o Markdown do usuário sem alterar; clean: aplica limpeza e heurísticas de Markdown")
    parser.add_argument("-i", "--input", required=True, help="Caminho do arquivo de entrada (.txt, .md, .pdf com texto embutido)")
    parser.add_argument("-s", "--source", required=True, help="Slug estável usado nos IDs dos chunks (ex: calculo-vol1)")
    parser.add_argument("-d", "--domain", required=True, help="Domínio de isolamento (matematica, courier, eletronica)")
    parser.add_argument("-o", "--output", required=True, help="Diretório de saída dos artefatos")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CONFIG.chunk_size_chars, help=f"Tamanho do chunk em caracteres (padrão: {DEFAULT_CONFIG.chunk_size_chars})")
    parser.add_argument("--chunk-overlap", type=int, default=DEFAULT_CONFIG.chunk_overlap_chars, help=f"Overlap entre chunks em caracteres (padrão: {DEFAULT_CONFIG.chunk_overlap_chars})")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    config = PipelineConfig(
        chunk_size_chars=args.chunk_size,
        chunk_overlap_chars=args.chunk_overlap,
        max_file_size_bytes=DEFAULT_CONFIG.max_file_size_bytes,
    )

    try:
        result = run_pipeline(
            input_path=args.input,
            source=args.source,
            domain=args.domain,
            mode=args.mode,
            output_dir=args.output,
            config=config,
        )
    except (ExtractError, PipelineError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1

    print(f"OK — {result.chunk_count} chunks gerados a partir de '{args.input}'")
    print(f"  Markdown tratado: {result.markdown_path}")
    print(f"  Chunks (legibilidade humana): {result.chunks_dir}/")
    print(f"  Payload ChromaDB: {result.payload_path}")
    print(f"  Manifest: {result.manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
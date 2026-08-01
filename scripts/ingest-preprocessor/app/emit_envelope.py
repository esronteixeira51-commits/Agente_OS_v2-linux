"""
Monta uma Message Envelope (01-ARCHITECTURE/Contrato_Interfaces.md) a
partir do chromadb_payload.json gerado pelo pipeline, pronta para
POST em /v1/dispatch do agent-os-api.

Este script conhece o FORMATO da Envelope, mas não importa
app.schemas do agent-os-api — o pré-processador roda no host, fora
do container, como um cliente HTTP comum (mesmo princípio de ADR-0005:
quem fala Envelope é a camada de integração, não a ferramenta em si).

Uso:
    python -m app.emit_envelope -p ./out/chromadb_payload.json -d matematica > envelope.json
    curl -s -X POST http://localhost:8080/v1/dispatch \\
        -H 'Content-Type: application/json' -d @envelope.json
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

from app.config import VALID_DOMAINS


def build_envelope(payload: dict, domain: str, permission_level: str = "execute_sandboxed", trace_id: str | None = None) -> dict:
    if domain not in VALID_DOMAINS:
        raise ValueError(f"domain '{domain}' inválido. Válidos: {sorted(VALID_DOMAINS)}")

    return {
        "trace_id": trace_id or str(uuid.uuid4()),
        "layer_from": "runtime",
        "layer_to": "tool",
        "target_id": "tool.chromadb_add",
        "payload": payload,
        "context": {"domain": domain},
        "permissions": {"level": permission_level},
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="emit_envelope",
        description="Monta a Message Envelope a partir de um chromadb_payload.json, para POST em /v1/dispatch",
    )
    parser.add_argument("-p", "--payload", required=True, help="Caminho do chromadb_payload.json gerado pelo pipeline")
    parser.add_argument("-d", "--domain", required=True, help="Domínio de isolamento (matematica, courier, eletronica)")
    parser.add_argument("--permission-level", default="execute_sandboxed", help="Nível de permissão (padrão: execute_sandboxed — tool.chromadb_add rejeita read_only)")
    parser.add_argument("--trace-id", default=None, help="trace_id explícito (padrão: gera um UUID novo)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    with open(args.payload, encoding="utf-8") as f:
        payload = json.load(f)

    try:
        envelope = build_envelope(payload, args.domain, args.permission_level, args.trace_id)
    except ValueError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(envelope, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
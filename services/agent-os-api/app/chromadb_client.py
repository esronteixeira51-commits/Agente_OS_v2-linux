"""
Wrapper sobre o ChromaDB (papel "vector-db", ver ADR-0003). Único
arquivo do agent-os-api que sabe que a tecnologia por trás desse papel
é ChromaDB especificamente — trocar de motor de busca vetorial
significa reescrever só este arquivo, nada em dispatcher.py muda de
assinatura.

Usa a função de embedding PADRÃO do próprio ChromaDB (all-MiniLM-L6-v2,
rodando via ONNX em CPU) de propósito — não pedimos embeddings ao LM
Studio. Isso preserva a VRAM da RTX 5050 inteira para o motor de
raciocínio principal.

O cliente oficial do ChromaDB é síncrono (bloqueante) — por isso o
dispatcher chama estas funções sempre via asyncio.to_thread, nunca
diretamente dentro de uma função async.

v2.0: porta quase 1:1 do v0.1.1 — nenhum bug encontrado aqui na
análise pré-reconstrução.
"""

from __future__ import annotations

from typing import Optional

import chromadb

from app.config import settings


class VectorDBError(Exception):
    """Levantado quando o vector-db está fora do ar ou retorna erro."""


def _get_client() -> "chromadb.HttpClient":
    return chromadb.HttpClient(host=settings.vector_db_host, port=settings.vector_db_port)


def _collection_name(domain: str) -> str:
    # Uma coleção por domínio — isolamento FÍSICO, não só lógico
    # (ADR-0008). O prefixo "kb_" evita colisão com qualquer coleção
    # futura que não siga essa convenção de nomes.
    return f"kb_{domain}"


def add_documents(
    domain: str,
    documents: list[str],
    ids: list[str],
    metadatas: Optional[list[dict]] = None,
) -> int:
    """Ingestão de documentos na coleção do domínio informado."""
    try:
        client = _get_client()
        collection = client.get_or_create_collection(name=_collection_name(domain))
        collection.add(documents=documents, ids=ids, metadatas=metadatas)
    except Exception as exc:  # ChromaDB pode levantar vários tipos distintos; normalizamos aqui
        raise VectorDBError(f"Falha ao adicionar documentos em '{domain}': {exc}") from exc
    return len(documents)


def search(domain: str, query_text: str, n_results: int = 5) -> list[dict]:
    """Busca por similaridade semântica dentro da coleção do domínio."""
    try:
        client = _get_client()
        collection = client.get_or_create_collection(name=_collection_name(domain))
        raw = collection.query(query_texts=[query_text], n_results=n_results)
    except Exception as exc:
        raise VectorDBError(f"Falha ao buscar em '{domain}': {exc}") from exc

    documents = raw.get("documents", [[]])[0]
    metadatas = raw.get("metadatas", [[]])[0]
    distances = raw.get("distances", [[]])[0]
    ids = raw.get("ids", [[]])[0]

    results = []
    for doc_id, text, metadata, distance in zip(ids, documents, metadatas, distances):
        results.append(
            {
                "id": doc_id,
                "text": text,
                "metadata": metadata or {},
                # Distância de cosseno — quanto MENOR, mais parecido.
                # Fica exposto bruto aqui; quem decide o que é "bom o
                # suficiente" é quem consome o resultado (Agent),
                # nunca o Tool.
                "distance": round(distance, 4),
            }
        )
    return results
"""
Testes de app.chromadb_client — mocka chromadb.HttpClient inteiro
(client + collection), sem precisar de um servidor ChromaDB real
rodando. O que importa testar aqui é a tradução entre a API do
ChromaDB e o formato de retorno do nosso contrato, não o ChromaDB em
si (isso é responsabilidade dos testes deles).
"""

from __future__ import annotations

import pytest

from app.chromadb_client import VectorDBError, _collection_name, add_documents, search


class _FakeCollection:
    def __init__(self, query_response=None, raise_on_add=None, raise_on_query=None):
        self.added_documents = None
        self.added_ids = None
        self.added_metadatas = None
        self._query_response = query_response or {
            "ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]],
        }
        self._raise_on_add = raise_on_add
        self._raise_on_query = raise_on_query

    def add(self, documents, ids, metadatas=None):
        if self._raise_on_add:
            raise self._raise_on_add
        self.added_documents = documents
        self.added_ids = ids
        self.added_metadatas = metadatas

    def query(self, query_texts, n_results):
        if self._raise_on_query:
            raise self._raise_on_query
        return self._query_response


class _FakeClient:
    def __init__(self, collection: _FakeCollection):
        self._collection = collection
        self.requested_collection_names: list[str] = []

    def get_or_create_collection(self, name):
        self.requested_collection_names.append(name)
        return self._collection


def _patch_client(monkeypatch, collection: _FakeCollection) -> _FakeClient:
    fake_client = _FakeClient(collection)
    monkeypatch.setattr("app.chromadb_client.chromadb.HttpClient", lambda **kwargs: fake_client)
    return fake_client


class TestCollectionName:
    def test_uses_kb_prefix_for_domain_isolation(self):
        assert _collection_name("matematica") == "kb_matematica"
        assert _collection_name("courier") == "kb_courier"


class TestAddDocuments:
    def test_returns_count_of_documents_added(self, monkeypatch):
        collection = _FakeCollection()
        _patch_client(monkeypatch, collection)

        count = add_documents(domain="matematica", documents=["a", "b", "c"], ids=["1", "2", "3"])
        assert count == 3

    def test_calls_collection_add_with_correct_arguments(self, monkeypatch):
        collection = _FakeCollection()
        _patch_client(monkeypatch, collection)

        add_documents(
            domain="matematica", documents=["texto"], ids=["id1"], metadatas=[{"source": "livro"}],
        )
        assert collection.added_documents == ["texto"]
        assert collection.added_ids == ["id1"]
        assert collection.added_metadatas == [{"source": "livro"}]

    def test_uses_correct_collection_for_domain(self, monkeypatch):
        collection = _FakeCollection()
        fake_client = _patch_client(monkeypatch, collection)

        add_documents(domain="eletronica", documents=["x"], ids=["1"])
        assert fake_client.requested_collection_names == ["kb_eletronica"]

    def test_exception_wrapped_in_vectordberror(self, monkeypatch):
        collection = _FakeCollection(raise_on_add=RuntimeError("chroma fora do ar"))
        _patch_client(monkeypatch, collection)

        with pytest.raises(VectorDBError, match="matematica"):
            add_documents(domain="matematica", documents=["x"], ids=["1"])


class TestSearch:
    def test_returns_parsed_results(self, monkeypatch):
        collection = _FakeCollection(query_response={
            "ids": [["doc1", "doc2"]],
            "documents": [["conteúdo 1", "conteúdo 2"]],
            "metadatas": [[{"source": "a"}, {"source": "b"}]],
            "distances": [[0.123456, 0.654321]],
        })
        _patch_client(monkeypatch, collection)

        results = search(domain="matematica", query_text="pergunta")
        assert len(results) == 2
        assert results[0] == {"id": "doc1", "text": "conteúdo 1", "metadata": {"source": "a"}, "distance": 0.1235}

    def test_empty_results_returns_empty_list(self, monkeypatch):
        collection = _FakeCollection()
        _patch_client(monkeypatch, collection)

        results = search(domain="matematica", query_text="nada encontrado")
        assert results == []

    def test_missing_metadata_defaults_to_empty_dict(self, monkeypatch):
        collection = _FakeCollection(query_response={
            "ids": [["doc1"]], "documents": [["texto"]], "metadatas": [[None]], "distances": [[0.5]],
        })
        _patch_client(monkeypatch, collection)

        results = search(domain="matematica", query_text="x")
        assert results[0]["metadata"] == {}

    def test_uses_correct_collection_for_domain(self, monkeypatch):
        collection = _FakeCollection()
        fake_client = _patch_client(monkeypatch, collection)

        search(domain="courier", query_text="x")
        assert fake_client.requested_collection_names == ["kb_courier"]

    def test_exception_wrapped_in_vectordberror(self, monkeypatch):
        collection = _FakeCollection(raise_on_query=RuntimeError("timeout"))
        _patch_client(monkeypatch, collection)

        with pytest.raises(VectorDBError, match="matematica"):
            search(domain="matematica", query_text="x")
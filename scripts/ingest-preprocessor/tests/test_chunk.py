from __future__ import annotations

import pytest

from app.chunk import chunk_markdown


class TestChunkByHeading:
    def test_single_section_no_heading(self):
        chunks = chunk_markdown("Texto sem heading nenhum.", chunk_size_chars=1000, chunk_overlap_chars=100)
        assert len(chunks) == 1
        assert chunks[0].heading_path == ""

    def test_splits_at_heading_boundaries(self):
        text = "# Capítulo 1\n\nConteúdo um.\n\n# Capítulo 2\n\nConteúdo dois."
        chunks = chunk_markdown(text, chunk_size_chars=1000, chunk_overlap_chars=100)
        assert len(chunks) == 2
        assert chunks[0].heading_path == "Capítulo 1"
        assert chunks[1].heading_path == "Capítulo 2"

    def test_nested_heading_path_is_hierarchical(self):
        text = "# Capítulo 1\n\nIntro.\n\n## 1.1 Seção\n\nDetalhe."
        chunks = chunk_markdown(text, chunk_size_chars=1000, chunk_overlap_chars=100)
        paths = [c.heading_path for c in chunks]
        assert "Capítulo 1" in paths
        assert "Capítulo 1 > 1.1 Seção" in paths

    def test_new_h1_closes_previous_h2(self):
        text = "# Cap 1\n\nA\n\n## 1.1\n\nB\n\n# Cap 2\n\nC"
        chunks = chunk_markdown(text, chunk_size_chars=1000, chunk_overlap_chars=100)
        paths = [c.heading_path for c in chunks]
        # "Cap 2" não deveria carregar "1.1" pendurado do capítulo anterior
        assert "Cap 2" in paths
        assert not any(p.startswith("Cap 1 > 1.1") and "Cap 2" in p for p in paths)

    def test_chunk_index_is_sequential_across_document(self):
        text = "# A\n\nx\n\n# B\n\ny"
        chunks = chunk_markdown(text, chunk_size_chars=1000, chunk_overlap_chars=100)
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


class TestChunkSlidingWindow:
    def test_large_section_is_split_into_multiple_chunks(self):
        content = "x" * 1000
        chunks = chunk_markdown(content, chunk_size_chars=300, chunk_overlap_chars=50)
        assert len(chunks) > 1
        assert all(len(c.text) <= 300 for c in chunks)

    def test_consecutive_chunks_overlap_by_configured_amount(self):
        content = "0123456789" * 50  # 500 chars determinísticos
        chunks = chunk_markdown(content, chunk_size_chars=200, chunk_overlap_chars=50)
        # o final do chunk 0 deve reaparecer no início do chunk 1
        overlap_expected = chunks[0].text[-50:]
        assert chunks[1].text.startswith(overlap_expected)

    def test_overlap_must_be_smaller_than_chunk_size(self):
        with pytest.raises(ValueError, match="menor que"):
            chunk_markdown("x" * 500, chunk_size_chars=100, chunk_overlap_chars=100)

    def test_small_section_is_not_split(self):
        chunks = chunk_markdown("texto curto", chunk_size_chars=2800, chunk_overlap_chars=350)
        assert len(chunks) == 1


class TestChunkEmptyContent:
    def test_blank_sections_are_skipped(self):
        text = "# Vazio\n\n\n\n# Com conteúdo\n\nTexto real."
        chunks = chunk_markdown(text, chunk_size_chars=1000, chunk_overlap_chars=100)
        assert len(chunks) == 1
        assert chunks[0].heading_path == "Com conteúdo"

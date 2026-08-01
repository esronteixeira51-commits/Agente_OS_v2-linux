from __future__ import annotations

import json

import pytest

from app.config import PipelineConfig
from app.pipeline import PipelineError, run_pipeline


class TestRunPipelinePassthrough:
    def test_preserves_markdown_unchanged(self, tmp_path):
        src = tmp_path / "artigo.md"
        src.write_text("# Título\n\nTexto já organizado pelo usuário.", encoding="utf-8")

        result = run_pipeline(
            input_path=str(src), source="artigo-teste", domain="matematica",
            mode="passthrough", output_dir=str(tmp_path / "out"),
        )

        markdown_content = (tmp_path / "out" / "markdown" / "artigo-teste.md").read_text(encoding="utf-8")
        assert markdown_content == "# Título\n\nTexto já organizado pelo usuário."
        assert result.chunk_count >= 1

    def test_does_not_apply_clean_or_markdownize_heuristics(self, tmp_path):
        # "42" isolado seria removido pelo clean() — no passthrough, não deve ser.
        src = tmp_path / "artigo.md"
        src.write_text("Linha um.\n\n42\n\nLinha dois.", encoding="utf-8")

        run_pipeline(
            input_path=str(src), source="teste", domain="matematica",
            mode="passthrough", output_dir=str(tmp_path / "out"),
        )
        markdown_content = (tmp_path / "out" / "markdown" / "teste.md").read_text(encoding="utf-8")
        assert "42" in markdown_content


class TestRunPipelineClean:
    def test_applies_clean_and_markdownize(self, tmp_path):
        src = tmp_path / "capitulo.txt"
        src.write_text("Capítulo 1 - Introdução\n\nTexto do capítulo.\n\n7\n\nMais texto.", encoding="utf-8")

        run_pipeline(
            input_path=str(src), source="livro-cap1", domain="matematica",
            mode="clean", output_dir=str(tmp_path / "out"),
        )
        markdown_content = (tmp_path / "out" / "markdown" / "livro-cap1.md").read_text(encoding="utf-8")
        assert markdown_content.startswith("# Capítulo 1: Introdução")
        assert "\n7\n" not in markdown_content  # número de página removido


class TestRunPipelineValidation:
    def test_invalid_domain_raises(self, tmp_path):
        src = tmp_path / "x.md"
        src.write_text("texto", encoding="utf-8")
        with pytest.raises(PipelineError, match="domain"):
            run_pipeline(str(src), "x", "financeiro", "passthrough", str(tmp_path / "out"))

    def test_invalid_mode_raises(self, tmp_path):
        src = tmp_path / "x.md"
        src.write_text("texto", encoding="utf-8")
        with pytest.raises(PipelineError, match="mode"):
            run_pipeline(str(src), "x", "matematica", "turbo", str(tmp_path / "out"))

    def test_empty_source_raises(self, tmp_path):
        src = tmp_path / "x.md"
        src.write_text("texto", encoding="utf-8")
        with pytest.raises(PipelineError, match="source"):
            run_pipeline(str(src), "  ", "matematica", "passthrough", str(tmp_path / "out"))


class TestRunPipelineArtifacts:
    def test_writes_all_expected_artifacts(self, tmp_path):
        src = tmp_path / "artigo.md"
        src.write_text("# T\n\nConteúdo.", encoding="utf-8")
        out_dir = tmp_path / "out"

        result = run_pipeline(str(src), "artigo", "matematica", "passthrough", str(out_dir))

        assert (out_dir / "markdown" / "artigo.md").exists()
        assert (out_dir / "chromadb_payload.json").exists()
        assert (out_dir / "manifest.json").exists()
        assert list((out_dir / "chunks").glob("*.md"))

    def test_chromadb_payload_has_matching_lengths(self, tmp_path):
        src = tmp_path / "artigo.md"
        src.write_text("# A\n\nUm.\n\n# B\n\nDois.", encoding="utf-8")
        out_dir = tmp_path / "out"

        result = run_pipeline(str(src), "artigo", "matematica", "passthrough", str(out_dir))

        payload = json.loads((out_dir / "chromadb_payload.json").read_text(encoding="utf-8"))
        assert len(payload["documents"]) == len(payload["ids"]) == len(payload["metadatas"])
        assert len(payload["documents"]) == result.chunk_count

    def test_ids_follow_source_chunk_pattern(self, tmp_path):
        src = tmp_path / "artigo.md"
        src.write_text("Conteúdo único.", encoding="utf-8")
        out_dir = tmp_path / "out"

        run_pipeline(str(src), "meu-artigo", "matematica", "passthrough", str(out_dir))
        payload = json.loads((out_dir / "chromadb_payload.json").read_text(encoding="utf-8"))
        assert payload["ids"][0] == "meu-artigo_chunk0000"

    def test_metadatas_include_domain_and_heading_path(self, tmp_path):
        src = tmp_path / "artigo.md"
        src.write_text("# Cap 1\n\nTexto.", encoding="utf-8")
        out_dir = tmp_path / "out"

        run_pipeline(str(src), "artigo", "eletronica", "passthrough", str(out_dir))
        payload = json.loads((out_dir / "chromadb_payload.json").read_text(encoding="utf-8"))
        assert payload["metadatas"][0]["domain"] == "eletronica"
        assert payload["metadatas"][0]["heading_path"] == "Cap 1"

    def test_manifest_records_config_and_counts(self, tmp_path):
        src = tmp_path / "artigo.md"
        src.write_text("Conteúdo.", encoding="utf-8")
        out_dir = tmp_path / "out"

        run_pipeline(
            str(src), "artigo", "matematica", "passthrough", str(out_dir),
            config=PipelineConfig(chunk_size_chars=500, chunk_overlap_chars=50),
        )
        manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["source"] == "artigo"
        assert manifest["domain"] == "matematica"
        assert manifest["mode"] == "passthrough"
        assert manifest["config"]["chunk_size_chars"] == 500
        assert manifest["chunk_count"] >= 1

    def test_custom_chunk_size_is_honored(self, tmp_path):
        src = tmp_path / "artigo.md"
        src.write_text("x" * 1000, encoding="utf-8")
        out_dir = tmp_path / "out"

        result = run_pipeline(
            str(src), "artigo", "matematica", "passthrough", str(out_dir),
            config=PipelineConfig(chunk_size_chars=300, chunk_overlap_chars=50),
        )
        assert result.chunk_count > 1

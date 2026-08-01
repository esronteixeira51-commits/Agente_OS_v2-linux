from __future__ import annotations

from app.cli import main


class TestCliSuccess:
    def test_passthrough_run_returns_zero_and_prints_summary(self, tmp_path, capsys):
        src = tmp_path / "artigo.md"
        src.write_text("# Título\n\nConteúdo.", encoding="utf-8")
        out_dir = tmp_path / "out"

        exit_code = main([
            "--mode", "passthrough", "-i", str(src), "-s", "artigo",
            "-d", "matematica", "-o", str(out_dir),
        ])

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "OK" in captured.out
        assert "chunks gerados" in captured.out

    def test_custom_chunk_size_flags_are_forwarded(self, tmp_path, capsys):
        src = tmp_path / "artigo.md"
        src.write_text("x" * 1000, encoding="utf-8")
        out_dir = tmp_path / "out"

        exit_code = main([
            "--mode", "passthrough", "-i", str(src), "-s", "artigo",
            "-d", "matematica", "-o", str(out_dir),
            "--chunk-size", "300", "--chunk-overlap", "50",
        ])
        assert exit_code == 0


class TestCliErrors:
    def test_nonexistent_input_returns_nonzero_with_message_on_stderr(self, tmp_path, capsys):
        exit_code = main([
            "--mode", "passthrough", "-i", str(tmp_path / "fantasma.md"),
            "-s", "x", "-d", "matematica", "-o", str(tmp_path / "out"),
        ])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "Erro" in captured.err

    def test_invalid_domain_returns_nonzero(self, tmp_path):
        src = tmp_path / "artigo.md"
        src.write_text("texto", encoding="utf-8")
        exit_code = main([
            "--mode", "passthrough", "-i", str(src), "-s", "x",
            "-d", "financeiro", "-o", str(tmp_path / "out"),
        ])
        assert exit_code == 1

from __future__ import annotations

import pytest
from pypdf import PdfWriter

from app.extract import ExtractError, extract


class TestExtractPlainText:
    def test_reads_txt_file(self, tmp_path):
        f = tmp_path / "artigo.txt"
        f.write_text("Conteúdo de teste", encoding="utf-8")
        assert extract(str(f), max_file_size_bytes=1_000_000) == "Conteúdo de teste"

    def test_reads_md_file(self, tmp_path):
        f = tmp_path / "artigo.md"
        f.write_text("# Título\n\nTexto", encoding="utf-8")
        assert extract(str(f), max_file_size_bytes=1_000_000) == "# Título\n\nTexto"

    def test_nonexistent_file_raises(self, tmp_path):
        with pytest.raises(ExtractError, match="não encontrado"):
            extract(str(tmp_path / "fantasma.txt"), max_file_size_bytes=1_000_000)

    def test_unsupported_extension_raises(self, tmp_path):
        f = tmp_path / "arquivo.docx"
        f.write_text("x", encoding="utf-8")
        with pytest.raises(ExtractError, match="não suportada"):
            extract(str(f), max_file_size_bytes=1_000_000)

    def test_empty_file_raises(self, tmp_path):
        f = tmp_path / "vazio.txt"
        f.write_text("", encoding="utf-8")
        with pytest.raises(ExtractError, match="vazio"):
            extract(str(f), max_file_size_bytes=1_000_000)

    def test_file_over_size_limit_raises(self, tmp_path):
        f = tmp_path / "grande.txt"
        f.write_text("x" * 100, encoding="utf-8")
        with pytest.raises(ExtractError, match="acima do limite"):
            extract(str(f), max_file_size_bytes=10)

    def test_non_utf8_file_raises_clear_error(self, tmp_path):
        f = tmp_path / "latin1.txt"
        f.write_bytes("café".encode("latin-1"))  # inválido como UTF-8
        with pytest.raises(ExtractError, match="UTF-8"):
            extract(str(f), max_file_size_bytes=1_000_000)


class TestExtractPdf:
    def _make_pdf_with_text(self, path, text: str = "Texto de teste no PDF"):
        # PdfWriter puro não desenha texto real facilmente sem uma lib
        # de renderização — para o teste de "tem texto extraível",
        # usamos reportlab se disponível; caso contrário, este teste
        # específico é pulado (o caminho de "sem texto" é coberto de
        # qualquer forma pelo teste abaixo, que é o caso mais crítico
        # de verificar: erro claro orientando para OCR).
        pytest.importorskip("reportlab")
        from reportlab.pdfgen import canvas

        c = canvas.Canvas(str(path))
        c.drawString(100, 750, text)
        c.save()

    def test_extracts_text_from_pdf_with_text_layer(self, tmp_path):
        f = tmp_path / "com_texto.pdf"
        self._make_pdf_with_text(f, "Ola mundo")
        result = extract(str(f), max_file_size_bytes=1_000_000)
        assert "Ola mundo" in result

    def test_image_only_pdf_raises_error_orienting_to_ocr(self, tmp_path):
        f = tmp_path / "so_imagem.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)  # página sem nenhum texto
        with open(f, "wb") as out:
            writer.write(out)

        with pytest.raises(ExtractError, match="ocr-worker"):
            extract(str(f), max_file_size_bytes=1_000_000)

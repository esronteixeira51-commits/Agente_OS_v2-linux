from __future__ import annotations

from app.clean import clean


class TestClean:
    def test_removes_page_number_only_lines(self):
        text = "Parágrafo um.\n\n42\n\nParágrafo dois."
        result = clean(text)
        assert "42" not in result.split("\n")

    def test_keeps_numbers_that_are_part_of_a_sentence(self):
        text = "O ano é 2024 e o valor é 42."
        assert clean(text) == text

    def test_rejoins_hyphenated_linebreak_word(self):
        text = "Este é um exemplo de pala-\nvra quebrada."
        result = clean(text)
        assert "palavra quebrada" in result
        assert "pala-\nvra" not in result

    def test_normalizes_windows_linebreaks(self):
        text = "linha1\r\nlinha2\r\n"
        result = clean(text)
        assert "\r" not in result

    def test_collapses_excess_blank_lines(self):
        text = "Um\n\n\n\n\nDois"
        result = clean(text)
        assert "\n\n\n" not in result
        assert "Um\n\nDois" == result

    def test_strips_leading_and_trailing_whitespace(self):
        assert clean("   \n\ntexto\n\n   ") == "texto"

    def test_nfkc_normalizes_unicode(self):
        # "café" com acento combinante (NFD) deve virar forma composta (NFC/NFKC)
        decomposed = "cafe\u0301"
        result = clean(decomposed)
        assert result == "café"

from __future__ import annotations

from app.markdownize import markdownize


class TestChapterDetection:
    def test_capitulo_with_title_becomes_h1(self):
        result = markdownize("Capítulo 3 - Derivadas\n\nTexto.")
        assert result.startswith("# Capítulo 3: Derivadas")

    def test_capitulo_without_title_becomes_h1(self):
        result = markdownize("Capítulo 3\n\nTexto.")
        assert result.startswith("# Capítulo 3")

    def test_uppercase_without_accent_also_detected(self):
        result = markdownize("CAPITULO IV - Integrais\n\nTexto.")
        assert result.startswith("# Capítulo IV: Integrais")


class TestNumberedSectionDetection:
    def test_two_level_section_becomes_h2(self):
        result = markdownize("3.1 Definição\n\nTexto.")
        assert result.startswith("## 3.1 Definição")

    def test_three_level_section_becomes_h3(self):
        result = markdownize("3.1.2 Exemplos\n\nTexto.")
        assert result.startswith("### 3.1.2 Exemplos")

    def test_section_with_trailing_dot(self):
        result = markdownize("3.1. Definição\n\nTexto.")
        assert result.startswith("## 3.1 Definição")


class TestShortHeadingDetection:
    def test_short_isolated_line_becomes_h2(self):
        text = "Parágrafo anterior.\n\nIntrodução\n\nParágrafo seguinte."
        result = markdownize(text)
        assert "## Introdução" in result

    def test_long_line_not_treated_as_heading(self):
        text = "Anterior.\n\nEsta linha tem muitas palavras demais para ser um título curto de verdade.\n\nSeguinte."
        result = markdownize(text)
        assert "## Esta linha" not in result

    def test_line_ending_in_period_not_treated_as_heading(self):
        text = "Anterior.\n\nFrase curta.\n\nSeguinte."
        result = markdownize(text)
        assert "## Frase curta." not in result

    def test_line_without_blank_line_around_not_treated_as_heading(self):
        text = "Introdução\nContinuação direto, sem linha em branco."
        result = markdownize(text)
        assert "## Introdução" not in result

    def test_existing_heading_not_reprocessed(self):
        text = "# Já é um heading"
        result = markdownize(text)
        assert result == "# Já é um heading"


class TestUnrecognizedTextPassesThrough:
    def test_plain_paragraph_unchanged(self):
        text = "Este é um parágrafo normal de texto corrido, sem nenhuma estrutura especial."
        assert markdownize(text) == text

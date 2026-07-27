"""
Testes do calculator_engine.py.

Duas classes no final (TestPowFunctionMagnitudeProtection e
TestComplexAndDomainErrorsAreStructured) existem especificamente para
travar os dois bugs encontrados na análise do v0.1.1 — se algum
refactor futuro reabrir qualquer um dos dois, é aqui que o CI aponta.
"""

from __future__ import annotations

import pytest

from app.calculator_engine import CalculatorError, MAX_EXPRESSION_LENGTH, evaluate


# ---------------------------------------------------------------------------
# Operações básicas
# ---------------------------------------------------------------------------

class TestBasicOperations:
    @pytest.mark.parametrize(
        "expression,expected",
        [
            ("2 + 2", 4),
            ("10 - 3", 7),
            ("6 * 7", 42),
            ("2 ** 10", 1024),
            ("17 % 5", 2),
            ("17 // 5", 3),
            ("-5 + 2", -3),
            ("abs(-8)", 8),
            ("min(3, 1, 2)", 1),
            ("max(3, 1, 2)", 3),
            ("round(3.7)", 4),
        ],
    )
    def test_integer_results(self, expression, expected):
        assert evaluate(expression)["value"] == expected

    def test_division_returns_float(self):
        assert evaluate("10 / 4")["value"] == 2.5

    def test_sqrt_of_positive_number(self):
        assert evaluate("sqrt(16)")["value"] == 4.0

    def test_operator_precedence_respected(self):
        assert evaluate("2 + 3 * 4")["value"] == 14


# ---------------------------------------------------------------------------
# Análise de dígitos (só para resultado inteiro)
# ---------------------------------------------------------------------------

class TestDigitAnalysis:
    def test_digit_fields_present_for_integer_result(self):
        result = evaluate("12345")
        assert result["digit_count"] == 5
        assert result["first_digits"] == "12345"
        assert result["last_digits"] == "12345"
        assert result["digit_sum"] == 1 + 2 + 3 + 4 + 5

    def test_digit_fields_absent_for_float_result(self):
        result = evaluate("10 / 4")
        assert "digit_count" not in result

    def test_digit_count_for_large_power(self):
        # 2**100 tem 31 dígitos — fácil de conferir de cabeça/calculadora
        result = evaluate("2 ** 100")
        assert result["digit_count"] == len(str(2**100))


# ---------------------------------------------------------------------------
# Segurança — allowlist de sintaxe
# ---------------------------------------------------------------------------

class TestSyntaxAllowlist:
    def test_rejects_expression_over_max_length(self):
        with pytest.raises(CalculatorError):
            evaluate("1" + "+1" * MAX_EXPRESSION_LENGTH)

    def test_rejects_invalid_syntax(self):
        with pytest.raises(CalculatorError):
            evaluate("2 +")

    def test_rejects_disallowed_function_name(self):
        with pytest.raises(CalculatorError):
            evaluate("__import__('os').system('echo hi')")

    def test_rejects_name_lookup(self):
        with pytest.raises(CalculatorError):
            evaluate("x + 1")

    def test_rejects_named_arguments(self):
        with pytest.raises(CalculatorError):
            evaluate("round(3.14159, ndigits=2)")

    def test_rejects_excessive_ast_depth(self):
        # Parênteses puros não criam profundidade de AST — são só
        # agrupamento sintático, o Python já "achata" isso no parse.
        # Quem cria nós aninhados de verdade é um operador repetido,
        # como 40 sinais de menos unário encadeados.
        deep_expression = "-" * 40 + "1"
        with pytest.raises(CalculatorError):
            evaluate(deep_expression)

    def test_zero_division_raises(self):
        with pytest.raises(ZeroDivisionError):
            evaluate("1 / 0")


# ---------------------------------------------------------------------------
# BUG #1 (v0.1.1): pow() como função não tinha a proteção de magnitude
# que o operador ** já tinha.
# ---------------------------------------------------------------------------

class TestPowFunctionMagnitudeProtection:
    def test_operator_form_blocks_huge_power(self):
        with pytest.raises(CalculatorError):
            evaluate("9 ** 999999999999")

    def test_function_form_blocks_huge_power_too(self):
        # Esta é a regressão do bug: no v0.1.1 esta linha NÃO
        # levantava CalculatorError e travava o processo tentando
        # calcular um inteiro com bilhões de dígitos.
        with pytest.raises(CalculatorError):
            evaluate("pow(9, 999999999999)")

    def test_function_form_still_works_for_safe_values(self):
        assert evaluate("pow(2, 10)")["value"] == 1024


# ---------------------------------------------------------------------------
# BUG #2 (v0.1.1): resultado complexo e erro de domínio matemático
# vazavam sem virar CalculatorError estruturado.
# ---------------------------------------------------------------------------

class TestComplexAndDomainErrorsAreStructured:
    def test_sqrt_of_negative_raises_calculator_error(self):
        # No v0.1.1 isso levantava um ValueError cru do módulo math,
        # sem nunca virar um erro do contrato.
        with pytest.raises(CalculatorError):
            evaluate("sqrt(-1)")

    def test_negative_base_fractional_exponent_raises_calculator_error(self):
        # No v0.1.1 isso retornava um `complex`, que quebrava mais
        # tarde ao tentar serializar em JSON, longe da causa real.
        with pytest.raises(CalculatorError):
            evaluate("(-8) ** 0.5")

    def test_pow_function_negative_base_fractional_exponent_also_raises(self):
        with pytest.raises(CalculatorError):
            evaluate("pow(-8, 0.5)")

    def test_negative_base_integer_exponent_still_works(self):
        # Base negativa com expoente INTEIRO é real e válido — só o
        # caso fracionário deve ser rejeitado.
        assert evaluate("(-2) ** 3")["value"] == -8
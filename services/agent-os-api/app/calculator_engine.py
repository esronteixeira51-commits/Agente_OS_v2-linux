"""
Motor de cálculo determinístico — a Tool que existe para que o LLM
NUNCA precise "narrar" aritmética como se fosse confiável (Manifesto,
Princípio 2: "cálculos nunca serão feitos pelo LLM").

Decisão de segurança central: NUNCA usar eval()/exec() do Python
diretamente sobre a expressão recebida — isso executaria qualquer
código Python, um risco real mesmo vindo de um LLM "de confiança"
(prompt injection, alucinação, etc.). Em vez disso, percorremos a
árvore sintática (ast) manualmente, permitindo SÓ os nós explicitamente
listados abaixo. Qualquer coisa fora da allowlist é rejeitada antes de
qualquer execução acontecer.

v2.0 — dois bugs corrigidos em relação ao v0.1.1 (achados na análise de
código + PCM sobre o dump original):

  1. `pow(a, b)` chamado como FUNÇÃO passava batido pela proteção de
     explosão de dígitos — só o operador `a ** b` tinha a checagem.
     Agora `_check_pow_magnitude` é chamada nos dois caminhos.
  2. Base negativa com expoente fracionário (ex: `(-8) ** 0.5`) produz
     um `complex` em Python — que não é serializável em JSON e antes
     vazava like um erro genérico lá na frente, longe de onde o
     problema realmente estava. `sqrt(-1)` também vazava um
     `ValueError` cru do `math`, sem virar um erro estruturado do
     contrato. Os dois agora viram `CalculatorError` no ponto exato
     onde acontecem.
"""

from __future__ import annotations

import ast
import math
import operator

MAX_EXPRESSION_LENGTH = 500
MAX_AST_DEPTH = 30

# Limite de proteção contra negação de serviço por magnitude: Python
# calcula inteiros arbitrariamente grandes sem limite nativo, então
# uma expressão como "9 ** 999999999999" tentaria gerar um número com
# bilhões de dígitos, travando o processo. Este limite é generoso o
# suficiente para casos reais (ex: 123456789**123456 tem ~999 mil
# dígitos, bem dentro do limite) mas bloqueia magnitudes absurdas.
MAX_RESULT_DIGITS = 2_000_000


class CalculatorError(Exception):
    """Levantado para qualquer expressão inválida ou não permitida."""


_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_ALLOWED_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# Lista fechada de funções permitidas — qualquer nome fora daqui é
# rejeitado. Deliberadamente pequena.
_ALLOWED_FUNCS = {
    "sqrt": math.sqrt,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "pow": pow,
}


def _check_pow_magnitude(base: float, exponent: float) -> None:
    """
    Estima o número de dígitos do resultado de uma potenciação ANTES
    de calcular de verdade — checar depois seria tarde demais, o
    próprio cálculo já teria consumido tempo/memória.

    Compartilhada pelos dois caminhos que podem gerar uma
    potenciação: o operador `**` (ast.Pow) e a função `pow(a, b)`
    (ast.Call). No v0.1.1 só o operador tinha essa proteção.
    """
    if isinstance(base, (int, float)) and isinstance(exponent, (int, float)) and abs(base) > 1 and exponent > 1:
        estimated_digits = exponent * math.log10(abs(base))
        if estimated_digits > MAX_RESULT_DIGITS:
            raise CalculatorError(
                f"Resultado estimado tem mais de {MAX_RESULT_DIGITS} dígitos — operação bloqueada por segurança"
            )


def _reject_complex(value):
    """
    Rejeita resultado complexo (só pode vir de uma potenciação com
    base negativa e expoente fracionário, ex: (-8) ** 0.5) como um
    CalculatorError estruturado, em vez de deixar o `complex` vazar
    até tentar (e falhar) serializar em JSON lá na frente.
    """
    if isinstance(value, complex):
        raise CalculatorError(
            "Resultado não é um número real — base negativa com expoente "
            "fracionário não é suportado por esta calculadora"
        )
    return value


def _eval_node(node: ast.AST, depth: int) -> float:
    if depth > MAX_AST_DEPTH:
        raise CalculatorError("Expressão excede a profundidade máxima permitida")

    if isinstance(node, ast.Expression):
        return _eval_node(node.body, depth + 1)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise CalculatorError(f"Constante não numérica não permitida: {node.value!r}")

    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_BINOPS:
            raise CalculatorError(f"Operador não permitido: {op_type.__name__}")
        left = _eval_node(node.left, depth + 1)
        right = _eval_node(node.right, depth + 1)

        if op_type is ast.Pow:
            _check_pow_magnitude(left, right)

        result = _ALLOWED_BINOPS[op_type](left, right)

        if op_type is ast.Pow:
            result = _reject_complex(result)

        return result

    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_UNARYOPS:
            raise CalculatorError(f"Operador unário não permitido: {op_type.__name__}")
        return _ALLOWED_UNARYOPS[op_type](_eval_node(node.operand, depth + 1))

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
            raise CalculatorError("Chamada de função não permitida")
        if node.keywords:
            raise CalculatorError("Argumentos nomeados não são permitidos")
        func_name = node.func.id
        args = [_eval_node(arg, depth + 1) for arg in node.args]

        if func_name == "pow" and len(args) == 2:
            _check_pow_magnitude(args[0], args[1])

        try:
            result = _ALLOWED_FUNCS[func_name](*args)
        except ValueError as exc:
            # ex: sqrt(-1) -> "math domain error" cru do módulo math.
            raise CalculatorError(f"Erro de domínio matemático em '{func_name}': {exc}") from exc

        if func_name == "pow":
            result = _reject_complex(result)

        return result

    # Qualquer outro tipo de nó (Name livre, Attribute, Subscript,
    # Lambda, comprehensions, Import, etc.) é rejeitado por padrão —
    # allowlist explícita, nunca denylist.
    raise CalculatorError(f"Elemento de expressão não permitido: {type(node).__name__}")


def evaluate(expression: str) -> dict:
    """
    Retorna um dicionário com o valor calculado e, quando o resultado
    é um inteiro, uma análise de dígitos calculada de forma 100%
    determinística (contagem, primeiros/últimos dígitos, soma) — isso
    existe porque LLMs são notoriamente ruins em manipulação de
    dígitos de números muito grandes (ex: contar quantos dígitos um
    número com centenas de milhares de dígitos possui). Nunca se deve
    pedir isso ao LLM; é sempre esta função que calcula.
    """
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise CalculatorError(f"Expressão excede o limite de {MAX_EXPRESSION_LENGTH} caracteres")

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise CalculatorError(f"Expressão inválida: {exc}") from exc

    value = _eval_node(tree, depth=0)

    result: dict = {"value": value}

    if isinstance(value, int):
        digits = str(abs(value))
        result["digit_count"] = len(digits)
        result["first_digits"] = digits[:25]
        result["last_digits"] = digits[-25:]
        result["digit_sum"] = sum(int(d) for d in digits)

    return result
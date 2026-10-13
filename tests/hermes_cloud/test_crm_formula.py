"""Tests for :mod:`hermes.crm.formula` — the safe formula engine.

``evaluate`` parses and computes a same-record arithmetic expression
with NO ``eval``/``exec``. ``extract_refs`` returns the set of field
identifiers an expression depends on. All math is ``Decimal``.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from hermes.crm.formula import FormulaError, evaluate, extract_refs


# ---- literals & arithmetic ------------------------------------------

def test_eval_integer_literal():
    assert evaluate("42", {}) == Decimal(42)


def test_eval_decimal_literal():
    assert evaluate("3.5", {}) == Decimal("3.5")


def test_eval_addition():
    assert evaluate("1 + 2 + 3", {}) == Decimal(6)


def test_eval_subtraction_left_assoc():
    assert evaluate("10 - 3 - 2", {}) == Decimal(5)


def test_eval_multiplication():
    assert evaluate("4 * 5", {}) == Decimal(20)


def test_eval_division():
    assert evaluate("12 / 4", {}) == Decimal(3)


def test_eval_precedence_mul_over_add():
    assert evaluate("2 + 3 * 4", {}) == Decimal(14)


def test_eval_parentheses_override_precedence():
    assert evaluate("(2 + 3) * 4", {}) == Decimal(20)


def test_eval_unary_minus():
    assert evaluate("-5 + 8", {}) == Decimal(3)


def test_eval_unary_minus_on_paren():
    assert evaluate("-(2 + 3)", {}) == Decimal(-5)


def test_eval_nested_parens():
    assert evaluate("((1 + 2) * (3 + 4))", {}) == Decimal(21)


# ---- field references -----------------------------------------------

def test_eval_field_ref():
    assert evaluate("quantity * price", {"quantity": 3, "price": 10}) == Decimal(30)


def test_eval_missing_ref_is_zero():
    assert evaluate("quantity * price", {"quantity": 3}) == Decimal(0)


def test_eval_empty_string_ref_is_zero():
    assert evaluate("amount + 1", {"amount": ""}) == Decimal(1)


def test_eval_none_ref_is_zero():
    assert evaluate("amount + 1", {"amount": None}) == Decimal(1)


def test_eval_decimal_valued_ref():
    assert evaluate("a + b", {"a": Decimal("1.1"), "b": Decimal("2.2")}) == Decimal("3.3")


def test_eval_string_numeric_ref_coerced():
    assert evaluate("a * 2", {"a": "7"}) == Decimal(14)


# ---- functions ------------------------------------------------------

def test_eval_abs():
    assert evaluate("ABS(-9)", {}) == Decimal(9)


def test_eval_round_two_places():
    assert evaluate("ROUND(3.14159, 2)", {}) == Decimal("3.14")


def test_eval_round_zero_places():
    assert evaluate("ROUND(2.5, 0)", {}) == Decimal(2) or evaluate("ROUND(2.5, 0)", {}) == Decimal(3)


def test_eval_min():
    assert evaluate("MIN(3, 1, 2)", {}) == Decimal(1)


def test_eval_max():
    assert evaluate("MAX(3, 1, 2)", {}) == Decimal(3)


def test_eval_min_single_arg():
    assert evaluate("MIN(5)", {}) == Decimal(5)


def test_eval_nested_functions():
    assert evaluate("ROUND(MAX(1.117, 2.224), 1)", {}) == Decimal("2.2")


def test_eval_function_args_are_expressions():
    assert evaluate("ABS(2 - 9)", {}) == Decimal(7)


# ---- IF + comparisons -----------------------------------------------

def test_eval_if_true_branch():
    assert evaluate("IF(1 > 0, 100, 200)", {}) == Decimal(100)


def test_eval_if_false_branch():
    assert evaluate("IF(1 < 0, 100, 200)", {}) == Decimal(200)


def test_eval_if_gte():
    assert evaluate("IF(5 >= 5, 1, 0)", {}) == Decimal(1)


def test_eval_if_lte():
    assert evaluate("IF(4 <= 3, 1, 0)", {}) == Decimal(0)


def test_eval_if_eq():
    assert evaluate("IF(2 == 2, 1, 0)", {}) == Decimal(1)


def test_eval_if_neq():
    assert evaluate("IF(2 != 3, 1, 0)", {}) == Decimal(1)


def test_eval_if_with_field_condition():
    expr = "IF(score >= 60, 1, 0)"
    assert evaluate(expr, {"score": 75}) == Decimal(1)
    assert evaluate(expr, {"score": 40}) == Decimal(0)


def test_eval_if_branches_are_expressions():
    assert evaluate("IF(1 > 0, 2 * 3, 9)", {}) == Decimal(6)


def test_eval_nested_if():
    expr = "IF(a > 10, IF(a > 20, 3, 2), 1)"
    assert evaluate(expr, {"a": 25}) == Decimal(3)
    assert evaluate(expr, {"a": 15}) == Decimal(2)
    assert evaluate(expr, {"a": 5}) == Decimal(1)


# ---- errors ---------------------------------------------------------

def test_eval_division_by_zero_raises():
    with pytest.raises(FormulaError):
        evaluate("1 / 0", {})


def test_eval_division_by_zero_field_raises():
    with pytest.raises(FormulaError):
        evaluate("a / b", {"a": 5, "b": 0})


def test_eval_unknown_function_raises():
    with pytest.raises(FormulaError):
        evaluate("BOGUS(1)", {})


def test_eval_parse_error_unbalanced_paren_raises():
    with pytest.raises(FormulaError):
        evaluate("(1 + 2", {})


def test_eval_parse_error_trailing_operator_raises():
    with pytest.raises(FormulaError):
        evaluate("1 +", {})


def test_eval_parse_error_empty_expression_raises():
    with pytest.raises(FormulaError):
        evaluate("", {})


def test_eval_parse_error_garbage_raises():
    with pytest.raises(FormulaError):
        evaluate("1 @ 2", {})


def test_eval_comparison_outside_if_raises():
    with pytest.raises(FormulaError):
        evaluate("1 > 0", {})


def test_eval_round_wrong_arity_raises():
    with pytest.raises(FormulaError):
        evaluate("ROUND(1.5)", {})


def test_eval_if_wrong_arity_raises():
    with pytest.raises(FormulaError):
        evaluate("IF(1 > 0, 5)", {})


def test_eval_abs_wrong_arity_raises():
    with pytest.raises(FormulaError):
        evaluate("ABS(1, 2)", {})


def test_eval_min_zero_args_raises():
    with pytest.raises(FormulaError):
        evaluate("MIN()", {})


# ---- extract_refs ---------------------------------------------------

def test_extract_refs_simple():
    assert extract_refs("quantity * price") == {"quantity", "price"}


def test_extract_refs_no_refs():
    assert extract_refs("1 + 2 * 3") == set()


def test_extract_refs_dedups():
    assert extract_refs("a + a + b") == {"a", "b"}


def test_extract_refs_inside_functions():
    assert extract_refs("ROUND(IF(score > limit, base, 0), 2)") == {
        "score", "limit", "base",
    }


def test_extract_refs_excludes_function_names():
    # IF / ROUND / MIN / MAX / ABS are not field refs
    assert extract_refs("MIN(a, MAX(b, c))") == {"a", "b", "c"}


def test_extract_refs_parse_error_raises():
    with pytest.raises(FormulaError):
        extract_refs("a + (")


# ---- detect_cycles --------------------------------------------------

from hermes.crm.formula import detect_cycles  # noqa: E402


def test_detect_cycles_acyclic_returns_none():
    fields = {
        "total": "subtotal + tax",
        "tax": "subtotal * 0.1",
    }
    assert detect_cycles(fields) is None


def test_detect_cycles_no_formula_fields():
    assert detect_cycles({}) is None


def test_detect_cycles_self_reference():
    cycle = detect_cycles({"a": "a + 1"})
    assert cycle is not None
    assert "a" in cycle


def test_detect_cycles_two_field_cycle():
    cycle = detect_cycles({"a": "b + 1", "b": "a + 1"})
    assert cycle is not None
    assert set(cycle) >= {"a", "b"}


def test_detect_cycles_three_field_cycle():
    cycle = detect_cycles({"a": "b", "b": "c", "c": "a"})
    assert cycle is not None
    assert set(cycle) >= {"a", "b", "c"}


def test_detect_cycles_ignores_non_formula_refs():
    # quantity / price are plain fields, not in the formula-field set
    fields = {"total": "quantity * price"}
    assert detect_cycles(fields) is None


def test_detect_cycles_diamond_is_acyclic():
    fields = {
        "d": "b + c",
        "b": "a + 1",
        "c": "a + 2",
        "a": "base * 2",
    }
    assert detect_cycles(fields) is None


def test_detect_cycles_parse_error_raises():
    with pytest.raises(FormulaError):
        detect_cycles({"a": "b + ("})

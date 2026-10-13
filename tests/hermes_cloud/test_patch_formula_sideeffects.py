"""patch_module_field formula side-effects — pure helper coverage.

formula_expression_of(field_config) returns the formula expression
string, or "" when the field has no (or a blank) formula config. The
PATCH route uses it to detect an expression change.
"""
from __future__ import annotations

from hermes_cloud.app.crm.routes import formula_expression_of


def test_formula_expression_of_none_config():
    assert formula_expression_of(None) == ""


def test_formula_expression_of_no_formula_key():
    assert formula_expression_of({"label_i18n": {"en-US": "X"}}) == ""


def test_formula_expression_of_present():
    assert formula_expression_of({"formula": {"expression": "a + b"}}) == "a + b"


def test_formula_expression_of_blank_expression():
    assert formula_expression_of({"formula": {"expression": "  "}}) == ""


from hermes.crm.formula import detect_cycles


def test_patch_cycle_map_rejects_when_new_expr_closes_loop():
    # Field "a" currently "1"; "b" is "a + 1". Changing "a" to "b + 1"
    # closes a cycle — the formula_map the route assembles must trip
    # detect_cycles.
    formula_map = {"a": "b + 1", "b": "a + 1"}
    assert detect_cycles(formula_map) is not None

"""Tests for PUT /crm/modules/{m}/fields/order.

validate_reorder is the pure validation core: it checks that `order`
is exactly the set of non-hidden field names (no missing, no unknown,
no dupes). The route wraps it with auth + DB writes.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from hermes_cloud.app.crm.routes import validate_reorder


def test_validate_reorder_accepts_exact_permutation():
    # Same set, different order — valid.
    validate_reorder(["b", "a", "c"], {"a", "b", "c"})


def test_validate_reorder_rejects_unknown_name():
    with pytest.raises(HTTPException) as exc:
        validate_reorder(["a", "ghost"], {"a", "b"})
    assert exc.value.status_code == 400


def test_validate_reorder_rejects_missing_name():
    # "c" is non-hidden but absent from order — incomplete.
    with pytest.raises(HTTPException) as exc:
        validate_reorder(["a", "b"], {"a", "b", "c"})
    assert exc.value.status_code == 400


def test_validate_reorder_rejects_duplicate():
    with pytest.raises(HTTPException) as exc:
        validate_reorder(["a", "a", "b"], {"a", "b"})
    assert exc.value.status_code == 400


def test_validate_reorder_rejects_non_list():
    with pytest.raises(HTTPException) as exc:
        validate_reorder("a,b", {"a", "b"})
    assert exc.value.status_code == 400

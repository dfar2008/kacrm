"""Tests for :mod:`hermes.crm.autonumber`.

``format_number`` is pure Python (no DB) — it renders a config + a
sequence integer + a timestamp into the stored numbering string. The
Postgres SEQUENCE that supplies ``seq_value`` is created and advanced
elsewhere (``provision_field`` / ``save.py``); this module only formats.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from hermes.crm.autonumber import format_number

NOW = datetime(2026, 5, 20, 9, 30, 0)


# ---- prefix + zero-padding -------------------------------------------

def test_format_basic_prefix_and_padding():
    cfg = {"prefix": "SO-", "digits": 5, "start": 1, "date_token": ""}
    assert format_number(cfg, 1, NOW) == "SO-00001"


def test_format_seq_value_wider_than_digits_not_truncated():
    cfg = {"prefix": "SO-", "digits": 3, "start": 1, "date_token": ""}
    assert format_number(cfg, 123456, NOW) == "SO-123456"


def test_format_empty_prefix():
    cfg = {"prefix": "", "digits": 4, "start": 1, "date_token": ""}
    assert format_number(cfg, 42, NOW) == "0042"


def test_format_zero_digits_means_no_padding():
    cfg = {"prefix": "N", "digits": 0, "start": 1, "date_token": ""}
    assert format_number(cfg, 7, NOW) == "N7"


# ---- date tokens -----------------------------------------------------

def test_format_date_token_yyyy():
    cfg = {"prefix": "INV-", "digits": 4, "start": 1, "date_token": "YYYY"}
    assert format_number(cfg, 9, NOW) == "INV-20260009"


def test_format_date_token_yyyymm():
    cfg = {"prefix": "INV-", "digits": 4, "start": 1, "date_token": "YYYYMM"}
    assert format_number(cfg, 9, NOW) == "INV-2026050009"


def test_format_date_token_yyyymmdd():
    cfg = {"prefix": "T", "digits": 3, "start": 1, "date_token": "YYYYMMDD"}
    assert format_number(cfg, 9, NOW) == "T20260520009"


def test_format_empty_date_token_renders_nothing():
    cfg = {"prefix": "X-", "digits": 2, "start": 1, "date_token": ""}
    assert format_number(cfg, 3, NOW) == "X-03"


# ---- defaults / tolerance -------------------------------------------

def test_format_missing_keys_use_defaults():
    # all keys optional — prefix "", digits 0, date_token ""
    assert format_number({}, 5, NOW) == "5"


def test_format_unknown_date_token_renders_nothing():
    cfg = {"prefix": "A", "digits": 2, "start": 1, "date_token": "BOGUS"}
    assert format_number(cfg, 1, NOW) == "A01"


def test_format_digits_as_string_coerced():
    cfg = {"prefix": "S", "digits": "4", "start": 1, "date_token": ""}
    assert format_number(cfg, 8, NOW) == "S0008"

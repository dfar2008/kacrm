"""Root conftest for the tests/ tree.

Empty by design — its existence ensures pytest inserts ``tests/`` onto
``sys.path`` so shared helper modules (e.g. ``_db_url``) are importable
from any test module under this directory.
"""

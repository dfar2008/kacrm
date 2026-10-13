"""LocalDiskStore — content-addressed, idempotent, readable.

Runs without a DB or the hermes package (passes an explicit root, so the
config-derived default is never touched)."""
from __future__ import annotations

from hermes_cloud.app.attachments.storage import LocalDiskStore


def test_put_is_content_addressed_and_readable(tmp_path):
    store = LocalDiskStore(root=str(tmp_path))
    data = b"hello-pdf-bytes"
    sha = "a" * 64
    path = store.put(sha256=sha, ext="pdf", data=data, profile_id="7")
    assert path.endswith(f"{sha}.pdf")
    assert "/aa/" in path or path.split("/")[-2] == "aa"
    assert store.exists(path)
    with store.open(path) as fh:
        assert fh.read() == data


def test_put_is_idempotent(tmp_path):
    store = LocalDiskStore(root=str(tmp_path))
    sha = "b" * 64
    p1 = store.put(sha256=sha, ext="png", data=b"x", profile_id="7")
    p2 = store.put(sha256=sha, ext="png", data=b"x", profile_id="7")
    assert p1 == p2
    assert store.exists(p1)


def test_no_extension(tmp_path):
    store = LocalDiskStore(root=str(tmp_path))
    sha = "c" * 64
    path = store.put(sha256=sha, ext="", data=b"raw", profile_id="9")
    assert path.endswith(sha)
    assert store.exists(path)

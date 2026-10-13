"""Tests for hermes_patcher: auto-apply of local hermes patches.

Rationale: the ``hermes`` submodule points at upstream, so we can't bump its
pointer to include our local fixes. Instead we keep patches in
``hermes-patches/`` and re-apply them idempotently at hermes_cloud startup.
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

from hermes_cloud.app.hermes_patcher import (
    PatchApplyError,
    ensure_hermes_patches_applied,
)


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=path, check=True)


def _commit(path: Path, msg: str = "c") -> None:
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=path, check=True)


def _make_patch(repo: Path, relpath: str, old: str, new: str) -> str:
    """Write ``new`` into relpath (old was committed), capture diff, revert."""
    file_path = repo / relpath
    file_path.write_text(new)
    out = subprocess.run(
        ["git", "diff", "HEAD", "--", relpath],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    file_path.write_text(old)
    return out.stdout


def test_apply_to_clean_submodule_succeeds(tmp_path):
    hermes = tmp_path / "hermes"
    hermes.mkdir()
    (hermes / "greeting.txt").write_text("hello\n")
    _init_repo(hermes)
    _commit(hermes, "initial")

    patches = tmp_path / "hermes-patches"
    patches.mkdir()
    (patches / "01-greet.patch").write_text(
        _make_patch(hermes, "greeting.txt", "hello\n", "hello world\n")
    )

    ensure_hermes_patches_applied(hermes, patches)

    assert (hermes / "greeting.txt").read_text() == "hello world\n"


def test_apply_is_idempotent_when_already_applied(tmp_path):
    hermes = tmp_path / "hermes"
    hermes.mkdir()
    (hermes / "greeting.txt").write_text("hello\n")
    _init_repo(hermes)
    _commit(hermes, "initial")

    patches = tmp_path / "hermes-patches"
    patches.mkdir()
    (patches / "01-greet.patch").write_text(
        _make_patch(hermes, "greeting.txt", "hello\n", "hello world\n")
    )

    # First call applies.
    ensure_hermes_patches_applied(hermes, patches)
    # Second call must not double-apply or raise.
    ensure_hermes_patches_applied(hermes, patches)

    assert (hermes / "greeting.txt").read_text() == "hello world\n"


def test_conflict_raises_patchapplyerror(tmp_path):
    hermes = tmp_path / "hermes"
    hermes.mkdir()
    (hermes / "greeting.txt").write_text("hello\n")
    _init_repo(hermes)
    _commit(hermes, "initial")

    patches = tmp_path / "hermes-patches"
    patches.mkdir()
    # Patch was generated against ``hello`` but the file now says something else.
    (patches / "01-greet.patch").write_text(
        _make_patch(hermes, "greeting.txt", "hello\n", "hello world\n")
    )

    # Simulate upstream change that conflicts.
    (hermes / "greeting.txt").write_text("HI\n")
    _commit(hermes, "upstream change")

    with pytest.raises(PatchApplyError) as exc:
        ensure_hermes_patches_applied(hermes, patches)

    assert "01-greet.patch" in str(exc.value)


def test_missing_patches_dir_is_noop(tmp_path):
    hermes = tmp_path / "hermes"
    hermes.mkdir()
    _init_repo(hermes)
    (hermes / "x.txt").write_text("x\n")
    _commit(hermes, "c")

    # No hermes-patches directory. Must not raise.
    ensure_hermes_patches_applied(hermes, tmp_path / "does-not-exist")


def test_empty_patches_dir_is_noop(tmp_path):
    hermes = tmp_path / "hermes"
    hermes.mkdir()
    _init_repo(hermes)
    (hermes / "x.txt").write_text("x\n")
    _commit(hermes, "c")

    patches = tmp_path / "hermes-patches"
    patches.mkdir()
    # No .patch files in there.
    ensure_hermes_patches_applied(hermes, patches)


def test_multiple_patches_all_applied(tmp_path):
    """Two independent patches both get applied."""
    hermes = tmp_path / "hermes"
    hermes.mkdir()
    (hermes / "a.txt").write_text("a\n")
    (hermes / "b.txt").write_text("b\n")
    _init_repo(hermes)
    _commit(hermes, "initial")

    patches = tmp_path / "hermes-patches"
    patches.mkdir()
    (patches / "01.patch").write_text(_make_patch(hermes, "a.txt", "a\n", "A\n"))
    (patches / "02.patch").write_text(_make_patch(hermes, "b.txt", "b\n", "B\n"))

    ensure_hermes_patches_applied(hermes, patches)

    assert (hermes / "a.txt").read_text() == "A\n"
    assert (hermes / "b.txt").read_text() == "B\n"

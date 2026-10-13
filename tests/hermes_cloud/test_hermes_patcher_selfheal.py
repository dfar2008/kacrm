"""Tests for hermes_patcher self-heal behavior on submodule drift."""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

from hermes_cloud.app.hermes_patcher import (
    PatchApplyError,
    ensure_hermes_patches_applied,
)


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env_args = ["-c", "user.email=t@t", "-c", "user.name=t"]
    return subprocess.run(
        ["git", *env_args, *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def fake_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build a parent repo containing a hermes-like submodule directory.

    Returns (parent_dir, hermes_dir, patches_dir).

    The "submodule" is implemented as a normal git repo whose sha is recorded
    in the parent's tree as a gitlink (mode 160000) — exactly the layout
    real submodules use, without the ceremony of `git submodule add`.
    """
    parent = tmp_path / "parent"
    hermes = parent / "hermes"
    patches = parent / "patches"
    parent.mkdir()
    hermes.mkdir()
    patches.mkdir()

    # Inner repo (the "submodule")
    _git(["init", "-q", "-b", "main"], cwd=hermes)
    (hermes / "file.txt").write_text("hello\n")
    _git(["add", "file.txt"], cwd=hermes)
    _git(["commit", "-qm", "init"], cwd=hermes)
    inner_sha = _git(["rev-parse", "HEAD"], cwd=hermes).stdout.strip()

    # Outer repo with a gitlink pointing at inner_sha
    _git(["init", "-q", "-b", "main"], cwd=parent)
    # Use update-index to register a gitlink (mode 160000)
    _git(
        ["update-index", "--add", "--cacheinfo", f"160000,{inner_sha},hermes"],
        cwd=parent,
    )
    _git(["commit", "-qm", "add hermes gitlink"], cwd=parent)

    # Build a patch that turns "hello" -> "hello world"
    patch_text = textwrap.dedent(
        """\
        diff --git a/file.txt b/file.txt
        --- a/file.txt
        +++ b/file.txt
        @@ -1 +1 @@
        -hello
        +hello world
        """
    )
    (patches / "01-greet.patch").write_text(patch_text)

    return parent, hermes, patches


def test_clean_state_no_self_heal(fake_repo, caplog):
    parent, hermes, patches = fake_repo
    with caplog.at_level("INFO"):
        ensure_hermes_patches_applied(hermes, patches)
    # Patch was applied, no self-heal log line.
    assert (hermes / "file.txt").read_text() == "hello world\n"
    assert not any("self-heal" in r.message for r in caplog.records)


def test_already_applied_idempotent(fake_repo, caplog):
    parent, hermes, patches = fake_repo
    ensure_hermes_patches_applied(hermes, patches)
    # Working tree now has a modified file (uncommitted) — that IS dirty.
    # To simulate the "already applied AND committed" case we commit it.
    _git(["add", "file.txt"], cwd=hermes)
    _git(["commit", "-qm", "patch applied"], cwd=hermes)

    caplog.clear()
    with caplog.at_level("INFO"):
        ensure_hermes_patches_applied(hermes, patches)
    # Still hello world, no self-heal triggered.
    assert (hermes / "file.txt").read_text() == "hello world\n"
    assert not any("self-heal" in r.message for r in caplog.records)
    assert any("already applied" in r.message for r in caplog.records)


def test_dirty_working_tree_triggers_self_heal(fake_repo, caplog):
    parent, hermes, patches = fake_repo
    # Stray uncommitted file — drift indicator #1.
    (hermes / "stray.txt").write_text("garbage\n")
    # Also dirty the tracked file to ensure git status sees it.
    (hermes / "file.txt").write_text("corrupted\n")

    with caplog.at_level("INFO"):
        ensure_hermes_patches_applied(hermes, patches)

    # Self-heal ran, then the patch applied cleanly on top of the reset tree.
    assert (hermes / "file.txt").read_text() == "hello world\n"
    assert not (hermes / "stray.txt").exists()
    assert any("self-healing" in r.message for r in caplog.records)


def test_partial_apply_triggers_self_heal(fake_repo, caplog):
    """A previously-aborted run leaves a patch half-applied (file content
    drifted such that neither reverse nor forward checks pass)."""
    parent, hermes, patches = fake_repo
    # Simulate half-state: file content makes both reverse and forward
    # checks fail. Replace the content with something unrelated and commit
    # so the working tree is "clean" (status check passes) but the patch
    # cannot be reconciled.
    (hermes / "file.txt").write_text("totally different content\n")
    _git(["add", "file.txt"], cwd=hermes)
    _git(["commit", "-qm", "drift"], cwd=hermes)

    with caplog.at_level("INFO"):
        ensure_hermes_patches_applied(hermes, patches)

    # After self-heal (reset to gitlink sha) + apply, expect clean result.
    assert (hermes / "file.txt").read_text() == "hello world\n"
    assert any(
        "ambiguous" in r.message and "self-healing" in r.message
        for r in caplog.records
    )


def test_no_patches_dir_noop(tmp_path):
    # Should silently no-op rather than crash.
    ensure_hermes_patches_applied(tmp_path / "nope", tmp_path / "absent")


def test_unrecoverable_state_still_raises(tmp_path):
    """If there is no parent gitlink and the patch genuinely cannot apply,
    self-heal can't recover and we surface PatchApplyError."""
    hermes = tmp_path / "hermes"
    patches = tmp_path / "patches"
    hermes.mkdir()
    patches.mkdir()
    _git(["init", "-q", "-b", "main"], cwd=hermes)
    (hermes / "file.txt").write_text("nothing matching\n")
    _git(["add", "file.txt"], cwd=hermes)
    _git(["commit", "-qm", "init"], cwd=hermes)

    patch_text = textwrap.dedent(
        """\
        diff --git a/file.txt b/file.txt
        --- a/file.txt
        +++ b/file.txt
        @@ -1 +1 @@
        -hello
        +hello world
        """
    )
    (patches / "01.patch").write_text(patch_text)

    with pytest.raises(PatchApplyError):
        ensure_hermes_patches_applied(hermes, patches)

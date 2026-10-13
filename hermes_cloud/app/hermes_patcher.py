"""Idempotently apply local hermes patches at startup.

The ``hermes`` submodule points at upstream (``NousResearch/hermes-agent``),
so we cannot bump the submodule pointer to include our own fixes. Instead we
keep patch files in ``hermes-patches/`` and reapply them at hermes_cloud
startup. This is safe to run repeatedly:

* If a patch is already applied (``git apply --check --reverse`` succeeds),
  we skip it.
* If the submodule is clean and the patch applies forward, we apply it.
* Otherwise we raise :class:`PatchApplyError` so the operator notices rather
  than running on a half-patched tree.

Self-heal: if the working tree is dirty, or if any patch is in an ambiguous
state (neither already-applied nor cleanly forward-applicable), the patcher
hard-resets the submodule to its expected gitlink target and starts fresh.
This is intentionally conservative: clean states pass through untouched.
"""
from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class PatchApplyError(RuntimeError):
    """Raised when a patch file cannot be reconciled with the submodule state."""


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def _already_applied(hermes_dir: Path, patch: Path) -> bool:
    return _run(
        ["git", "apply", "--check", "--reverse", str(patch)], cwd=hermes_dir
    ).returncode == 0


def _can_apply_forward(hermes_dir: Path, patch: Path) -> bool:
    return _run(
        ["git", "apply", "--check", str(patch)], cwd=hermes_dir
    ).returncode == 0


def _hard_reset(hermes_dir: Path) -> None:
    """Hard reset ``hermes_dir`` to its expected submodule pointer.

    Looks up the gitlink target sha from the parent repo so the reset always
    matches what the cloud expects, regardless of whatever state the
    submodule's working HEAD has drifted to.
    """
    parent_repo = hermes_dir.parent
    expected_sha: str | None = None
    result = _run(
        ["git", "ls-tree", "HEAD", hermes_dir.name],
        cwd=parent_repo,
    )
    if result.returncode == 0:
        # Output format: "160000 commit <sha>\thermes\n"
        parts = result.stdout.split()
        if len(parts) >= 3:
            expected_sha = parts[2]

    if expected_sha:
        _run(["git", "reset", "--hard", expected_sha], cwd=hermes_dir)
    else:
        _run(["git", "reset", "--hard", "HEAD"], cwd=hermes_dir)
    _run(["git", "clean", "-fd"], cwd=hermes_dir)
    logger.info(
        "hermes patches: self-heal complete (reset to %s)",
        expected_sha or "HEAD",
    )


def _self_heal_if_dirty(hermes_dir: Path, patches: list[Path]) -> None:
    """Detect status drift and reset ``hermes_dir`` before applying patches.

    Drift indicators:
    1. Working tree has uncommitted changes (``git status --porcelain``
       non-empty).
    2. Any patch fails BOTH :func:`_already_applied` AND
       :func:`_can_apply_forward` — that is, we are in a half-applied
       middle state from a previously aborted run.

    Recovery: hard reset to the gitlink target + ``git clean -fd``.
    Logged at INFO so operators see the recovery happened.
    """
    # Check 1: dirty working tree?
    status = _run(["git", "status", "--porcelain"], cwd=hermes_dir)
    if status.stdout.strip():
        logger.info(
            "hermes patches: working tree dirty, self-healing "
            "(status=%r)",
            status.stdout.strip().splitlines()[:5],
        )
        _hard_reset(hermes_dir)
        return

    # Check 2: any patch ambiguous?
    for patch in patches:
        if _already_applied(hermes_dir, patch):
            continue
        if _can_apply_forward(hermes_dir, patch):
            # Clean forward-applicable state; nothing to heal.
            return
        # Neither reverse nor forward — drift detected.
        logger.info(
            "hermes patches: %s ambiguous (neither reverse nor forward "
            "checks pass), self-healing",
            patch.name,
        )
        _hard_reset(hermes_dir)
        return


def ensure_hermes_patches_applied(hermes_dir: Path, patches_dir: Path) -> None:
    """Apply every ``*.patch`` under ``patches_dir`` to ``hermes_dir``, idempotent.

    On status drift (uncommitted changes, half-applied middle state) the
    function self-heals by hard-resetting ``hermes_dir`` to its expected
    submodule pointer and starting clean. Only intervenes when patcher
    detects ambiguous state — clean states pass through untouched.

    Raises :class:`PatchApplyError` if a patch can neither be detected as
    already-applied nor applied cleanly forward, even after a self-heal.
    """
    if not patches_dir.exists():
        logger.debug("hermes patches dir %s does not exist; skipping", patches_dir)
        return

    patches = sorted(patches_dir.glob("*.patch"))
    if not patches:
        logger.debug("no hermes patches in %s; skipping", patches_dir)
        return

    # Self-heal pass — best-effort, never fatal.
    t_start = time.monotonic()
    healed = False
    try:
        _self_heal_if_dirty(hermes_dir, patches)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("hermes patches self-heal skipped: %s", exc)

    applied_count = 0
    already_count = 0
    applied_names: list[str] = []
    for patch in patches:
        if _already_applied(hermes_dir, patch):
            already_count += 1
            logger.info("hermes patch %s already applied; skipping", patch.name)
            continue
        if _can_apply_forward(hermes_dir, patch):
            result = _run(["git", "apply", str(patch)], cwd=hermes_dir)
            if result.returncode != 0:
                raise PatchApplyError(
                    f"failed to apply {patch.name}: {result.stderr.strip()}"
                )
            applied_count += 1
            applied_names.append(patch.name)
            logger.info("applied hermes patch %s", patch.name)
            continue
        raise PatchApplyError(
            f"cannot apply {patch.name}: hermes submodule is in an unexpected "
            f"state. Run `git -C {hermes_dir} status` to inspect."
        )

    # Single-line WARNING-level summary so the default Python log level
    # (WARNING) surfaces patcher activity in uvicorn / supervisor logs.
    # Without this, the per-patch INFO logs above are silently dropped,
    # leaving operators with no signal at boot time. The applied list is
    # included so deploy logs make it obvious whether a new patch landed.
    elapsed_ms = (time.monotonic() - t_start) * 1000
    if applied_count:
        logger.warning(
            "hermes patches: applied=%d already=%d total=%d in %.0fms "
            "(new this boot: %s)",
            applied_count, already_count, len(patches), elapsed_ms,
            ", ".join(applied_names),
        )
    else:
        logger.warning(
            "hermes patches: applied=0 already=%d total=%d in %.0fms (steady state)",
            already_count, len(patches), elapsed_ms,
        )

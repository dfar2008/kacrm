#!/usr/bin/env python3
"""Build KaCRM from a minimal source-only context, without local credentials/data."""
from __future__ import annotations
import argparse
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {"Dockerfile", ".dockerignore", "pyproject.toml", "LICENSE", "LICENSING.md",
              "alembic.ini", "hermes_prefork.py"}
SOURCE_DIRS = {"hermes_cloud", "alembic", "frontend", "hermes-patches"}
EXCLUDED = {".git", ".venv", "__pycache__", "node_modules", "dist", "logs", ".npmrc"}
HERMES_EXCLUDED = {"tests", "website", "apps", "evals", "contributors", "optional-skills",
                   ".github", ".claude", ".codex"}


def allowed(name: str) -> bool:
    p = PurePosixPath(name)
    return (not p.is_absolute() and ".." not in p.parts
            and not any(part in EXCLUDED or part == ".env" or part.startswith(".env.") for part in p.parts)
            and p.suffix not in {".pem", ".key", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3", ".dump", ".pyc"}
            and p.name not in {"auth.json", "credentials.json"}
            and "credentials" not in p.name.lower())


def prepare_context(destination: Path, root: Path = ROOT) -> None:
    paths = subprocess.check_output(
        ["git", "ls-files", "-z", "--", *sorted(SOURCE_DIRS), *sorted(ROOT_FILES)], cwd=root,
    ).decode().split("\0")
    # New container files are explicitly allowlisted even before their first commit.
    paths += list(ROOT_FILES)
    paths += [str(p.relative_to(root)) for p in (root / "deploy/container").glob("*") if p.is_file()]
    for name in sorted(set(paths)):
        if not name or not allowed(name):
            continue
        source = root / name
        if source.is_symlink() or not source.is_file():
            continue
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    # Export the parent's pinned gitlink, never local Hermes config or dirty files.
    gitlink = subprocess.check_output(["git", "ls-tree", "HEAD", "hermes"], cwd=root).decode().split()
    if len(gitlink) < 3 or gitlink[0] != "160000":
        raise RuntimeError("Initialize the pinned Hermes submodule before building")
    proc = subprocess.Popen(["git", "archive", "--format=tar", gitlink[2]],
                            cwd=root / "hermes", stdout=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as archive:
            for item in archive:
                if not item.isfile() or not allowed(item.name):
                    continue
                if PurePosixPath(item.name).parts[0] in HERMES_EXCLUDED:
                    continue
                target = destination / "hermes" / item.name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(item) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(item.mode & 0o777)
        if proc.wait() != 0:
            raise RuntimeError("Hermes source export failed")
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    print(f"Clean build context ready; Hermes {gitlink[2][:12]}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="kacrm:all-in-one-preview")
    parser.add_argument("--platform", help="e.g. linux/amd64 or linux/arm64; default is builder architecture")
    parser.add_argument("--target", choices=("application", "all-in-one"), default="all-in-one")
    parser.add_argument("--context-only", type=Path, help="Export clean context without building")
    args = parser.parse_args()
    if args.context_only:
        args.context_only.mkdir(parents=True, exist_ok=False)
        prepare_context(args.context_only)
        return
    with tempfile.TemporaryDirectory(prefix="kacrm-image-") as folder:
        context = Path(folder)
        prepare_context(context)
        cmd = ["docker", "build", "--target", args.target, "--tag", args.tag]
        if args.platform:
            cmd += ["--platform", args.platform]
        subprocess.run([*cmd, str(context)], check=True)


if __name__ == "__main__":
    main()

"""Apply reviewed overlays to an exported Hermes source tree, without Git metadata."""
from pathlib import Path
import subprocess
import shutil


def prepare(root: Path) -> None:
    hermes = root / "hermes"
    for patch in sorted((root / "hermes-patches").glob("*.patch")):
        if patch.name == "50-upstream-tests.patch":
            continue  # Upstream test adaptations are not application runtime.
        subprocess.run(["git", "apply", "--check", str(patch)], cwd=hermes, check=True)
        subprocess.run(["git", "apply", str(patch)], cwd=hermes, check=True)
    shutil.rmtree(hermes / "tests", ignore_errors=True)
    if not (hermes / "crm" / "db.py").is_file():
        raise RuntimeError("KaCRM overlay is missing")


if __name__ == "__main__":
    prepare(Path(__file__).resolve().parents[2])

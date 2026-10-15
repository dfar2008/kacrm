#!/usr/bin/env python3
"""Check local documentation image references and the screenshot inventory."""
from hashlib import sha256
from html import unescape
import json
from pathlib import Path
import re
import struct
from urllib.parse import unquote, urlsplit


def main():
    root = Path(__file__).resolve().parents[1]
    sources = list(root.glob("README*.md"))
    for folder in ["docs", "assets", "scripts", "deploy", "hermes-patches"]:
        sources.extend((root / folder).rglob("*.md"))
        sources.extend((root / folder).rglob("*.html"))
    errors, references = [], []
    for path in sources:
        source = path.read_text(encoding="utf-8")
        targets = re.findall(r"!\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)", source)
        targets += re.findall(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"']", source, re.I)
        for target in targets:
            url = urlsplit(unescape(target.strip("<>")))
            if url.scheme or url.netloc:
                continue
            asset = (path.parent / unquote(url.path)).resolve()
            references.append(asset)
            if not asset.is_file():
                errors.append(f"Missing image: {path.relative_to(root)} -> {target}")

    inventory = json.loads((root / "assets/user-guide/captures.json").read_text())
    captures = {item["file"]: item for item in inventory["captures"]}
    assert len(captures) == len(inventory["captures"]), "Duplicate inventory entries"
    registered = set(captures)
    for name, item in captures.items():
        data = (root / name).read_bytes()
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            errors.append(f"Invalid PNG: {name}")
            continue
        if sha256(data).hexdigest() != item["sha256"]:
            errors.append(f"Capture checksum mismatch: {name}")
        if struct.unpack(">II", data[16:24]) != (item["image_width"], item["image_height"]):
            errors.append(f"Capture dimensions mismatch: {name}")
    for item in inventory["copies"]:
        registered.add(item["file"])
        if (root / item["file"]).read_bytes() != (root / item["source"]).read_bytes():
            errors.append(f"Stale image copy: {item['file']}")
    for folder in ["assets/readme", "assets/user-guide"]:
        for path in (root / folder).rglob("*"):
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                continue
            name = str(path.relative_to(root))
            if name not in registered:
                errors.append(f"Unregistered or historical image: {name}")
            if path.resolve() not in references:
                errors.append(f"Unreferenced image: {name}")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"OK: {len(references)} references; {len(captures)} captures; "
          f"{len(inventory['copies'])} synchronized copies")


if __name__ == "__main__":
    main()

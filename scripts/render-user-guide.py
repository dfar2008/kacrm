#!/usr/bin/env python3
"""Render the Chinese print guide from its maintained Markdown source.

Requires the Markdown package: python3 -m pip install Markdown
"""
from html import unescape
from pathlib import Path
import re


def main():
    try:
        import markdown
    except ImportError:
        raise SystemExit("Install the renderer first: python3 -m pip install Markdown")
    root = Path(__file__).resolve().parents[1]
    source = root / "docs/user-guide.md"
    template = (root / "assets/user-guide/print-template.html").read_text(encoding="utf-8")

    def slugify(value, separator):
        value = re.sub(r"[^\w\s-]", "", unescape(value).lower())
        return re.sub(r"[-\s]+", separator, value).strip(separator)

    body = markdown.markdown(
        source.read_text(encoding="utf-8"), extensions=["extra", "toc"],
        extension_configs={"toc": {"slugify": slugify}},
    )
    output = template.replace("{{ content }}", body)
    (root / "docs/user-guide.html").write_text(output, encoding="utf-8")
    print("Rendered docs/user-guide.html")


if __name__ == "__main__":
    main()

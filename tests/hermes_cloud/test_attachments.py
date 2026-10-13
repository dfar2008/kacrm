"""Unit tests for :mod:`hermes_cloud.app.llm.attachments`.

Covers extract_text() and prepare_attachments() — pure functions that need
no database or network access.
"""
from __future__ import annotations

import base64
import io

import pytest

from hermes_cloud.app.llm.attachments import (
    _MAX_DOC_CHARS,
    _decode,
    build_user_content,
    extract_text,
    prepare_attachments,
)

# ---------------------------------------------------------------------------
# Minimal 1×1 transparent PNG — a known valid binary so image tests don't
# need pillow or a real image file.
# ---------------------------------------------------------------------------
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx"
    b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00"
    b"\x00\x00\x00IEND\xaeB`\x82"
)


# ---------------------------------------------------------------------------
# _decode
# ---------------------------------------------------------------------------


def test_decode_utf8():
    assert _decode("hello".encode("utf-8")) == "hello"


def test_decode_gb18030_chinese():
    chinese = "你好世界"
    encoded = chinese.encode("gb18030")
    # Must NOT be valid UTF-8 for this test to be meaningful
    try:
        encoded.decode("utf-8")
        pytest.skip("test string happens to be valid utf-8 — skip")
    except UnicodeDecodeError:
        pass
    assert _decode(encoded) == chinese


def test_decode_latin1_fallback():
    # Latin-1 byte sequence that is not valid UTF-8
    data = bytes([0xE9, 0xE0, 0xFC])
    result = _decode(data)
    assert isinstance(result, str)
    assert len(result) == 3


# ---------------------------------------------------------------------------
# extract_text — CSV
# ---------------------------------------------------------------------------


def _make_csv(*rows: tuple) -> bytes:
    buf = io.StringIO()
    import csv

    w = csv.writer(buf)
    for row in rows:
        w.writerow(row)
    return buf.getvalue().encode("utf-8")


def test_extract_text_csv_basic():
    data = _make_csv(("name", "age"), ("Alice", "30"), ("Bob", "25"))
    result = extract_text("data.csv", data)
    assert "name" in result
    assert "Alice" in result
    assert "Bob" in result


def test_extract_text_csv_tabs_between_cells():
    data = _make_csv(("x", "y", "z"),)
    result = extract_text("data.csv", data)
    # The cells should be tab-joined
    assert "x\ty\tz" in result


def test_extract_text_tsv():
    data = b"col1\tcol2\nval1\tval2\n"
    result = extract_text("data.tsv", data)
    assert "col1" in result
    assert "val2" in result


# ---------------------------------------------------------------------------
# extract_text — plain text
# ---------------------------------------------------------------------------


def test_extract_text_txt_utf8():
    payload = "Hello, world! 中文"
    result = extract_text("notes.txt", payload.encode("utf-8"))
    assert result == payload


def test_extract_text_unknown_extension_falls_back_to_decode():
    payload = "some raw content"
    result = extract_text("file.xyz", payload.encode("utf-8"))
    assert result == payload


# ---------------------------------------------------------------------------
# extract_text — xlsx
# ---------------------------------------------------------------------------


def _make_xlsx(*rows: tuple) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_extract_text_xlsx_contains_cell_values():
    data = _make_xlsx(("Product", "Price"), ("Widget", 9.99), ("Gadget", 14.50))
    result = extract_text("prices.xlsx", data)
    assert "Product" in result
    assert "Widget" in result
    assert "9.99" in result
    assert "Gadget" in result


def test_extract_text_xlsx_multiple_sheets():
    from openpyxl import Workbook

    wb = Workbook()
    ws1 = wb.active
    ws1.title = "Sheet1"
    ws1.append(["Alpha", "Beta"])
    ws2 = wb.create_sheet("Sheet2")
    ws2.append(["Gamma", "Delta"])
    buf = io.BytesIO()
    wb.save(buf)
    data = buf.getvalue()

    result = extract_text("multi.xlsx", data)
    assert "Alpha" in result
    assert "Gamma" in result
    # Sheet titles should appear as headings
    assert "Sheet1" in result
    assert "Sheet2" in result


# ---------------------------------------------------------------------------
# extract_text — docx
# ---------------------------------------------------------------------------


def _make_docx(*paragraphs: str) -> bytes:
    import docx

    doc = docx.Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_extract_text_docx_paragraphs():
    data = _make_docx("First paragraph.", "Second paragraph.", "Third.")
    result = extract_text("report.docx", data)
    assert "First paragraph." in result
    assert "Second paragraph." in result
    assert "Third." in result


def test_extract_text_docx_table():
    import docx

    doc = docx.Document()
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "HeaderA"
    table.cell(0, 1).text = "HeaderB"
    table.cell(1, 0).text = "Row1A"
    table.cell(1, 1).text = "Row1B"
    buf = io.BytesIO()
    doc.save(buf)
    data = buf.getvalue()

    result = extract_text("table.docx", data)
    assert "HeaderA" in result
    assert "Row1B" in result


# ---------------------------------------------------------------------------
# prepare_attachments — images
# ---------------------------------------------------------------------------


def test_prepare_attachments_image_by_extension():
    files = [("photo.png", _TINY_PNG, "image/png")]
    result = prepare_attachments(files)
    assert len(result) == 1
    item = result[0]
    assert item["kind"] == "image"
    assert item["filename"] == "photo.png"
    assert item["data_url"].startswith("data:image/png;base64,")


def test_prepare_attachments_image_data_url_roundtrip():
    files = [("photo.png", _TINY_PNG, "image/png")]
    item = prepare_attachments(files)[0]
    # Strip the prefix and decode — must get back original bytes
    prefix = "data:image/png;base64,"
    assert item["data_url"].startswith(prefix)
    decoded = base64.b64decode(item["data_url"][len(prefix):])
    assert decoded == _TINY_PNG


def test_prepare_attachments_image_by_content_type():
    # Extension is .bin but content_type says image/jpeg
    files = [("upload.bin", _TINY_PNG, "image/jpeg")]
    item = prepare_attachments(files)[0]
    assert item["kind"] == "image"
    assert "image/jpeg" in item["data_url"]


def test_prepare_attachments_jpeg_extension():
    files = [("shot.jpg", _TINY_PNG, "")]
    item = prepare_attachments(files)[0]
    assert item["kind"] == "image"


# ---------------------------------------------------------------------------
# prepare_attachments — documents
# ---------------------------------------------------------------------------


def test_prepare_attachments_document_txt():
    content = "This is a text document."
    files = [("readme.txt", content.encode("utf-8"), "text/plain")]
    result = prepare_attachments(files)
    assert len(result) == 1
    item = result[0]
    assert item["kind"] == "document"
    assert item["filename"] == "readme.txt"
    assert "This is a text document." in item["text"]


def test_prepare_attachments_document_csv():
    data = _make_csv(("a", "b"), ("1", "2"))
    files = [("data.csv", data, "text/csv")]
    item = prepare_attachments(files)[0]
    assert item["kind"] == "document"
    assert "a" in item["text"]


# ---------------------------------------------------------------------------
# prepare_attachments — truncation
# ---------------------------------------------------------------------------


def test_prepare_attachments_truncates_long_document():
    # Build a text that is definitely longer than _MAX_DOC_CHARS
    long_text = "A" * (_MAX_DOC_CHARS + 5000)
    files = [("big.txt", long_text.encode("utf-8"), "text/plain")]
    item = prepare_attachments(files)[0]
    assert item["kind"] == "document"
    # Text must be capped
    assert len(item["text"]) < len(long_text)
    # Must end with the truncation marker
    assert item["text"].endswith("\n…(truncated)")
    # The non-truncated portion must be exactly _MAX_DOC_CHARS chars
    truncation_marker = "\n…(truncated)"
    body = item["text"][: -len(truncation_marker)]
    assert len(body) == _MAX_DOC_CHARS


def test_prepare_attachments_no_truncation_at_boundary():
    # Exactly at limit — should NOT be truncated
    text = "B" * _MAX_DOC_CHARS
    files = [("exact.txt", text.encode("utf-8"), "text/plain")]
    item = prepare_attachments(files)[0]
    assert not item["text"].endswith("\n…(truncated)")
    assert item["text"] == text


# ---------------------------------------------------------------------------
# prepare_attachments — mixed batch
# ---------------------------------------------------------------------------


def test_prepare_attachments_mixed_batch():
    csv_data = _make_csv(("x",), ("1",))
    files = [
        ("photo.png", _TINY_PNG, "image/png"),
        ("data.csv", csv_data, "text/csv"),
    ]
    result = prepare_attachments(files)
    assert len(result) == 2
    kinds = {item["kind"] for item in result}
    assert kinds == {"image", "document"}


def test_prepare_attachments_empty_list():
    assert prepare_attachments([]) == []


# ---------------------------------------------------------------------------
# build_user_content
# ---------------------------------------------------------------------------


def test_build_user_content_no_attachments():
    result = build_user_content("hello", None)
    assert result == "hello"


def test_build_user_content_empty_attachments():
    result = build_user_content("hello", [])
    assert result == "hello"


def test_build_user_content_document_appended_to_text():
    attachments = [{"kind": "document", "filename": "notes.txt", "text": "doc content"}]
    result = build_user_content("question", attachments)
    assert isinstance(result, str)
    assert "question" in result
    assert "[附件:notes.txt]" in result
    assert "doc content" in result


def test_build_user_content_image_returns_multimodal_list():
    data_url = "data:image/png;base64,abc123"
    attachments = [{"kind": "image", "filename": "photo.png", "data_url": data_url}]
    result = build_user_content("describe this", attachments)
    assert isinstance(result, list)
    assert result[0] == {"type": "text", "text": "describe this"}
    assert result[1] == {"type": "image_url", "image_url": {"url": data_url}}


def test_build_user_content_mixed_doc_and_image():
    data_url = "data:image/png;base64,abc"
    attachments = [
        {"kind": "document", "filename": "readme.txt", "text": "doc text"},
        {"kind": "image", "filename": "photo.png", "data_url": data_url},
    ]
    result = build_user_content("check these", attachments)
    assert isinstance(result, list)
    # Text block should include both the user text and the doc content
    text_block = result[0]
    assert text_block["type"] == "text"
    assert "check these" in text_block["text"]
    assert "[附件:readme.txt]" in text_block["text"]
    assert "doc text" in text_block["text"]
    # Image block follows
    assert result[1]["type"] == "image_url"

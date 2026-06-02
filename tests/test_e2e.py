"""End-to-end tests for outofoffice.

Assumes the docker-compose stack at the repo root is up and
reachable at http://localhost:8000. The CI workflow builds the
image locally and brings the stack up before invoking pytest;
locally, ``docker compose up -d`` is enough.

The flow per format under test:

  1. Write a small UTF-8 .txt source to tmp_path.
  2. POST it to /to/<format>; expect 200 and a download URL.
  3. GET the download URL and verify the bytes' magic
     prefix matches the format (PDF: ``%PDF-``, ZIP-based
     OOXML / OpenDocument: ``PK\\x03\\x04``, HTML: ``<``).

Conversions exercised: pdf (the universal target), html
(text-based, fast), odt (LibreOffice's native), docx (the
most-requested MS format). That's a small subset of the 42
declared endpoints — running every conversion in CI would
balloon wall-time. test_config.py pins the rest of the
surface structurally; this suite is the integration smoke
that a careless YAML edit didn't break the wrapper or
LibreOffice's actual conversion path.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import requests

OUTOFOFFICE_BASE_URL = os.environ.get(
    "OUTOFOFFICE_BASE_URL", "http://localhost:8000"
)

# A tiny UTF-8 text body. Plain ASCII keeps every conversion
# path's font requirements minimal — Liberation + DejaVu (in
# the Dockerfile) cover this trivially.
SAMPLE_TEXT = (
    "Hello from outofoffice.\n"
    "This is a deterministic source document used by the "
    "E2E tests.\n"
    "\n"
    "If you can read this in the converted output, the "
    "round-trip works.\n"
)


@pytest.fixture(scope="module")
def source_text(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("doc") / "source.txt"
    path.write_text(SAMPLE_TEXT, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# liveness — the / endpoint outofoffice inherits from url2code
# ---------------------------------------------------------------------------


def test_liveness_returns_outofoffice_service():
    """``/`` reports ``service: outofoffice`` (not ``url2code``).
    The api.title -> service field wiring landed in
    url2code 1.0.6; this test pins the inheritance."""
    r = requests.get(OUTOFOFFICE_BASE_URL + "/", timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "outofoffice"
    assert body["status"] == "ok"
    assert body["version"]


# ---------------------------------------------------------------------------
# /formats — discovery
# ---------------------------------------------------------------------------


def test_formats_returns_curated_catalog():
    """GET /formats returns the catalog as parsed_output:
    a list of {ext, name, family} entries. Without it
    consumers can't discover supported extensions without
    reading the README."""
    r = requests.get(OUTOFOFFICE_BASE_URL + "/v1/formats", timeout=5)
    assert r.status_code == 200, r.text
    body = r.json()
    catalog = body.get("parsed_output")
    assert isinstance(catalog, list)
    assert len(catalog) >= 1
    for entry in catalog:
        assert {"ext", "name", "family"} <= set(entry.keys())
    # Every format used by the conversion tests below must
    # be in the catalog -- otherwise the endpoint shouldn't
    # exist and we'd get a 404 in those tests instead.
    exts = [e["ext"] for e in catalog]
    for ext in ("pdf", "html", "odt", "docx"):
        assert ext in exts, f"catalog missing {ext!r}"


# ---------------------------------------------------------------------------
# round-trip: upload -> convert -> fetch the converted bytes
# ---------------------------------------------------------------------------


def _convert(
    source: Path, target_format: str, data: dict | None = None,
) -> requests.Response:
    """POST the source to /to/<target_format> and return the
    raw response. Optional `data` carries extra form fields
    (e.g. export-profile flags). Caller asserts on it."""
    with open(source, "rb") as f:
        return requests.post(
            f"{OUTOFOFFICE_BASE_URL}/v1/to/{target_format}",
            files={"document": ("source.txt", f, "text/plain")},
            data=data or {},
            timeout=180,  # LibreOffice cold-start is slow
        )


def _download(response: requests.Response, key: str = "converted") -> bytes:
    """Pull the converted bytes from the response's
    download_url. The output_files placeholder in the YAML
    is ``output_path``, but url2code keys the response by the
    placeholder name minus the ``_path`` suffix when possible
    — handle the realistic shapes."""
    body = response.json()
    output_files = body.get("output_files") or {}
    # url2code keys by the YAML placeholder name; we declared
    # ``output_path``, so look that up directly.
    entry = output_files.get("output_path")
    assert entry, f"no output_path entry in response: {body}"
    download_url = entry.get("download_url")
    assert download_url, f"no download_url on entry: {entry}"

    # download_url may be an absolute URL or a path; normalize.
    if download_url.startswith("/"):
        download_url = OUTOFOFFICE_BASE_URL + download_url
    r = requests.get(download_url, timeout=60)
    assert r.status_code == 200, \
        f"download {download_url} failed: {r.status_code} {r.text[:200]}"
    return r.content


def test_convert_to_pdf(source_text):
    """``txt -> pdf`` is the universal smoke test. Output
    must start with ``%PDF-`` (the PDF magic)."""
    r = _convert(source_text, "pdf")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("exit_code") == 0, body

    converted = _download(r)
    assert len(converted) > 100, "PDF suspiciously small"
    assert converted.startswith(b"%PDF-"), \
        f"not a PDF (first bytes: {converted[:8]!r})"


def test_convert_to_pdfa(source_text):
    """``txt -> pdf`` with ``pdfa=yes`` -> a PDF/A-1b archival
    file. LibreOffice embeds a pdfaid XMP packet, so the bytes
    carry the ``pdfaid`` marker on top of the %PDF magic."""
    r = _convert(source_text, "pdf", data={"pdfa": "yes"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("exit_code") == 0, body
    converted = _download(r)
    assert converted.startswith(b"%PDF-"), \
        f"not a PDF (first bytes: {converted[:8]!r})"
    assert b"pdfaid" in converted, \
        "no pdfaid XMP marker -- PDF/A profile was not applied"


def test_convert_to_html(source_text):
    """``txt -> html`` -- output is text-based, so we can
    inspect the actual content of the source surfacing in
    the conversion."""
    r = _convert(source_text, "html")
    assert r.status_code == 200, r.text
    converted = _download(r)
    text = converted.decode("utf-8", errors="replace").lower()
    # LibreOffice's HTML export wraps in <html>...</html>.
    assert "<html" in text or "<!doctype html" in text, \
        f"not HTML (head: {text[:200]!r})"
    # The source body should appear somewhere in the output
    # (LibreOffice doesn't strip user text on round-trip).
    assert "outofoffice" in text


def test_convert_to_odt(source_text):
    """``txt -> odt`` -- LibreOffice's native format. ODT
    is a ZIP archive, so the magic is the ZIP signature."""
    r = _convert(source_text, "odt")
    assert r.status_code == 200, r.text
    converted = _download(r)
    assert converted.startswith(b"PK\x03\x04"), \
        f"not a ZIP/ODT (first bytes: {converted[:4]!r})"


def test_convert_to_docx(source_text):
    """``txt -> docx`` -- OOXML is a ZIP archive too."""
    r = _convert(source_text, "docx")
    assert r.status_code == 200, r.text
    converted = _download(r)
    assert converted.startswith(b"PK\x03\x04"), \
        f"not a ZIP/DOCX (first bytes: {converted[:4]!r})"

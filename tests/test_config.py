"""Static checks on config/tools.yaml.

outofoffice has no Python source of its own — the HTTP
surface is entirely declared in config/tools.yaml and
consumed by url2code at runtime. These tests pin the YAML
shape so a careless edit can't ship a malformed config that
only surfaces at container-start.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = REPO_ROOT / "config" / "tools.yaml"

# The full set of LibreOffice export filters outofoffice
# exposes. Adding a format means: extend this list AND add
# the matching endpoint to tools.yaml. Removing one means
# both. Drift between the two is what this test pins.
EXPECTED_FORMATS = [
    # text
    "pdf", "docx", "doc", "odt", "fodt", "ott",
    "rtf", "txt", "html", "xhtml", "epub", "uot",
    # spreadsheet
    "xlsx", "xls", "ods", "fods", "ots",
    "csv", "dbf", "dif", "slk",
    # presentation
    "pptx", "ppt", "odp", "fodp", "otp",
    # drawing
    "odg", "fodg", "otg", "svg", "emf", "wmf",
    # raster image
    "png", "jpg", "gif", "bmp", "tiff", "webp",
    # other
    "mml", "ps", "eps", "ltx",
]


@pytest.fixture(scope="module")
def cfg():
    return yaml.safe_load(CONFIG.read_text())


@pytest.fixture(scope="module")
def endpoints(cfg):
    return cfg["endpoints"]


# ---------------------------------------------------------------------------
# top-level shape
# ---------------------------------------------------------------------------


def test_yaml_parses(cfg):
    """Sentinel — if any commit lands a syntactically broken
    YAML, this fires before the image ever builds."""
    assert isinstance(cfg, dict)
    assert "endpoints" in cfg
    assert isinstance(cfg["endpoints"], list)


def test_top_level_metadata(cfg):
    assert cfg["api"]["title"] == "outofoffice"
    assert cfg["api"]["default_root"] == "/"
    assert cfg["logging"]["level"] in {"DEBUG", "INFO", "WARNING", "ERROR"}


def test_no_unexpected_endpoints(endpoints):
    """No admin or auxiliary endpoints — the surface is
    exactly one route per export format. If a future edit
    adds something unexpected, surface it here."""
    routes = {e["route"] for e in endpoints}
    expected_routes = {f"/to/{ext}" for ext in EXPECTED_FORMATS}
    extra = routes - expected_routes
    missing = expected_routes - routes
    assert not extra, f"unexpected routes: {sorted(extra)}"
    assert not missing, f"missing routes: {sorted(missing)}"


def test_endpoint_count_matches_format_catalog(endpoints):
    """The number of endpoints must equal the number of
    formats EXPECTED_FORMATS pins. Drift here means either
    the YAML or the catalog needs updating."""
    assert len(endpoints) == len(EXPECTED_FORMATS)


def test_routes_are_unique(endpoints):
    pairs = [(e.get("method", "GET"), e["route"]) for e in endpoints]
    assert len(pairs) == len(set(pairs)), f"duplicate routes: {pairs}"


def test_endpoint_names_are_unique(endpoints):
    names = [e["name"] for e in endpoints]
    assert len(names) == len(set(names)), f"duplicate names: {names}"


# ---------------------------------------------------------------------------
# command shape — every endpoint shells out to the wrapper
# ---------------------------------------------------------------------------


def test_every_endpoint_calls_soffice_convert(endpoints):
    """The wrapper at /app/bin/soffice-convert is the only
    binary outofoffice runs. Catching a stray executable
    here means the container never has to find out at
    request time."""
    for e in endpoints:
        assert e["command"]["executable"] == "/app/bin/soffice-convert", \
            f"{e['name']} uses unexpected executable"


def test_every_endpoint_passes_three_args(endpoints):
    """The wrapper takes exactly three args:
    ``<input_path> <output_path> <target_format>``. A missing
    or swapped arg would fail at request time — pin the
    arg-count and order here."""
    for e in endpoints:
        args = e["command"]["args"]
        assert len(args) == 3, \
            f"{e['name']} passes {len(args)} args, expected 3"
        assert args[0] == "{input_path}", \
            f"{e['name']} arg 0 must be the input placeholder"
        assert args[1] == "{output_path}", \
            f"{e['name']} arg 1 must be the output placeholder"


def test_format_arg_matches_route(endpoints):
    """The third wrapper arg is the target format (e.g.
    ``pdf``); it must match the trailing path component of
    the route. If they drift, /to/pdf would silently emit a
    .docx (or vice versa)."""
    for e in endpoints:
        route_format = e["route"].rsplit("/", 1)[-1]
        arg_format = e["command"]["args"][2]
        assert arg_format == route_format, \
            f"{e['name']} route says {route_format!r} but arg says {arg_format!r}"


def test_every_endpoint_has_a_timeout(endpoints):
    """LibreOffice can hang on malformed input. A missing
    timeout means the request would block forever; url2code
    inherits a default but pinning it here keeps drifts
    obvious. 30s is too low — even simple conversions can
    spend that on the first soffice cold-start."""
    for e in endpoints:
        timeout = e["command"].get("timeout_seconds")
        assert timeout is not None, f"{e['name']} missing timeout"
        assert timeout >= 60, \
            f"{e['name']} timeout {timeout}s too low for LibreOffice"


# ---------------------------------------------------------------------------
# upload shape — every endpoint takes the same multipart field
# ---------------------------------------------------------------------------


def test_every_endpoint_has_one_document_upload(endpoints):
    """Clients shouldn't have to remember a different field
    name per endpoint. ``document`` is the convention; pin
    it across the surface."""
    for e in endpoints:
        uploads = e.get("uploads") or []
        assert len(uploads) == 1, \
            f"{e['name']} has {len(uploads)} uploads, expected 1"
        upload = uploads[0]
        assert upload["field_name"] == "document"
        assert upload["placeholder"] == "input_path"


def test_input_placeholder_is_substituted(endpoints):
    for e in endpoints:
        assert "{input_path}" in e["command"]["args"], \
            f"{e['name']} missing {{input_path}} arg"


# ---------------------------------------------------------------------------
# output_files — the binary download URL is what the caller wants
# ---------------------------------------------------------------------------


def test_every_endpoint_declares_one_output_file(endpoints):
    """url2code generates a download URL only for declared
    output_files; without one, callers get JSON metadata but
    no way to fetch the converted bytes."""
    for e in endpoints:
        outputs = e.get("output_files") or []
        assert len(outputs) == 1, \
            f"{e['name']} has {len(outputs)} output_files, expected 1"
        out = outputs[0]
        assert out["placeholder"] == "output_path"
        assert out["filename_placeholder"] == "output_filename"


def test_output_suffix_matches_route(endpoints):
    """The output_files.suffix becomes the file extension on
    the generated download URL. It must match the route's
    target format so consumers downloading from /to/pdf get
    a file ending in ``.pdf``."""
    for e in endpoints:
        route_format = e["route"].rsplit("/", 1)[-1]
        suffix = e["output_files"][0]["suffix"]
        assert suffix == f".{route_format}", \
            f"{e['name']} suffix {suffix!r} != .{route_format}"


def test_output_placeholder_is_substituted(endpoints):
    """The output path is materialized by url2code before
    the wrapper runs. Without the placeholder the wrapper
    would try to write to the literal string."""
    for e in endpoints:
        assert "{output_path}" in e["command"]["args"], \
            f"{e['name']} missing {{output_path}} arg"


# ---------------------------------------------------------------------------
# output mode — text, because the wrapper's stdout is informational
# ---------------------------------------------------------------------------


def test_every_endpoint_uses_text_output_mode(endpoints):
    """The wrapper writes a single ``wrote <path>`` line on
    success and surfaces soffice errors on failure. There's
    no structured output for url2code to parse — text mode
    captures the line and trusts the exit code; the
    download_url in the JSON response is the actual
    deliverable."""
    for e in endpoints:
        assert e["output"]["mode"] == "text", \
            f"{e['name']} must use text output mode"

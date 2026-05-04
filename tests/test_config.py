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
FORMATS_YAML = REPO_ROOT / "config" / "formats.yaml"


def _load_catalog():
    return yaml.safe_load(FORMATS_YAML.read_text())


# The list of formats outofoffice exposes is sourced from
# config/formats.yaml (the canonical catalog). EXPECTED_FORMATS
# is derived at import time so adding an entry to the catalog
# automatically propagates here -- the per-format /to/<ext>
# endpoints in tools.yaml are then verified against the
# catalog by tests below, catching drift before it ships.
EXPECTED_FORMATS = [c["ext"] for c in _load_catalog()]
VALID_FAMILIES = {"text", "sheet", "slides", "drawing", "image", "other"}


@pytest.fixture(scope="module")
def cfg():
    return yaml.safe_load(CONFIG.read_text())


@pytest.fixture(scope="module")
def endpoints(cfg):
    return cfg["endpoints"]


@pytest.fixture(scope="module")
def convert_endpoints(endpoints):
    """Just the format-conversion endpoints (/to/<ext>).
    Excludes the /formats discovery endpoint, which has a
    fundamentally different shape (GET, no upload, runs
    cat-yaml-as-json instead of soffice-convert) and would
    fail every "every endpoint must..." assertion below."""
    return [e for e in endpoints if e["name"] != "formats"]


@pytest.fixture(scope="module")
def catalog():
    return _load_catalog()


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
    # The API surface lives under /v1/. Liveness ``/`` and
    # FastAPI's ``/docs`` stay at the root regardless.
    assert cfg["api"]["default_root"] == "/v1"
    assert cfg["logging"]["level"] in {"DEBUG", "INFO", "WARNING", "ERROR"}


def test_no_unexpected_endpoints(endpoints):
    """The surface is exactly one route per export format
    plus the /formats discovery endpoint. If a future edit
    adds something else, surface it here."""
    routes = {e["route"] for e in endpoints}
    expected_routes = {f"/to/{ext}" for ext in EXPECTED_FORMATS}
    expected_routes.add("/formats")
    extra = routes - expected_routes
    missing = expected_routes - routes
    assert not extra, f"unexpected routes: {sorted(extra)}"
    assert not missing, f"missing routes: {sorted(missing)}"


def test_endpoint_count_matches_format_catalog(convert_endpoints):
    """Number of /to/<ext> endpoints must equal the number
    of catalog entries. Drift here means either the YAML or
    the catalog needs updating."""
    assert len(convert_endpoints) == len(EXPECTED_FORMATS)


def test_routes_are_unique(endpoints):
    pairs = [(e.get("method", "GET"), e["route"]) for e in endpoints]
    assert len(pairs) == len(set(pairs)), f"duplicate routes: {pairs}"


def test_endpoint_names_are_unique(endpoints):
    names = [e["name"] for e in endpoints]
    assert len(names) == len(set(names)), f"duplicate names: {names}"


# ---------------------------------------------------------------------------
# command shape — every endpoint shells out to the wrapper
# ---------------------------------------------------------------------------


def test_every_convert_endpoint_calls_soffice_convert(convert_endpoints):
    """The wrapper at /app/bin/soffice-convert is the only
    binary the conversion endpoints run. (/formats uses
    cat-yaml-as-json and is excluded.)"""
    for e in convert_endpoints:
        assert e["command"]["executable"] == "/app/bin/soffice-convert", \
            f"{e['name']} uses unexpected executable"


def test_every_convert_endpoint_passes_three_args(convert_endpoints):
    """The wrapper takes exactly three args:
    ``<input_path> <output_path> <target_format>``. A missing
    or swapped arg would fail at request time — pin the
    arg-count and order here."""
    for e in convert_endpoints:
        args = e["command"]["args"]
        assert len(args) == 3, \
            f"{e['name']} passes {len(args)} args, expected 3"
        assert args[0] == "{input_path}", \
            f"{e['name']} arg 0 must be the input placeholder"
        assert args[1] == "{output_path}", \
            f"{e['name']} arg 1 must be the output placeholder"


def test_format_arg_matches_route(convert_endpoints):
    """The third wrapper arg is the target format (e.g.
    ``pdf``); it must match the trailing path component of
    the route. If they drift, /to/pdf would silently emit a
    .docx (or vice versa)."""
    for e in convert_endpoints:
        route_format = e["route"].rsplit("/", 1)[-1]
        arg_format = e["command"]["args"][2]
        assert arg_format == route_format, \
            f"{e['name']} route says {route_format!r} but arg says {arg_format!r}"


def test_every_convert_endpoint_has_a_timeout(convert_endpoints):
    """LibreOffice can hang on malformed input. A missing
    timeout means the request would block forever; url2code
    inherits a default but pinning it here keeps drifts
    obvious. 30s is too low — even simple conversions can
    spend that on the first soffice cold-start."""
    for e in convert_endpoints:
        timeout = e["command"].get("timeout_seconds")
        assert timeout is not None, f"{e['name']} missing timeout"
        assert timeout >= 60, \
            f"{e['name']} timeout {timeout}s too low for LibreOffice"


# ---------------------------------------------------------------------------
# upload shape — every endpoint takes the same multipart field
# ---------------------------------------------------------------------------


def test_every_convert_endpoint_has_one_document_upload(convert_endpoints):
    """Clients shouldn't have to remember a different field
    name per endpoint. ``document`` is the convention; pin
    it across the conversion surface. (/formats takes no
    upload and is excluded.)"""
    for e in convert_endpoints:
        uploads = e.get("uploads") or []
        assert len(uploads) == 1, \
            f"{e['name']} has {len(uploads)} uploads, expected 1"
        upload = uploads[0]
        assert upload["field_name"] == "document"
        assert upload["placeholder"] == "input_path"


def test_input_placeholder_is_substituted(convert_endpoints):
    for e in convert_endpoints:
        assert "{input_path}" in e["command"]["args"], \
            f"{e['name']} missing {{input_path}} arg"


# ---------------------------------------------------------------------------
# output_files — the binary download URL is what the caller wants
# ---------------------------------------------------------------------------


def test_every_convert_endpoint_declares_one_output_file(convert_endpoints):
    """url2code generates a download URL only for declared
    output_files; without one, callers get JSON metadata but
    no way to fetch the converted bytes. (/formats has no
    output_files and is excluded.)"""
    for e in convert_endpoints:
        outputs = e.get("output_files") or []
        assert len(outputs) == 1, \
            f"{e['name']} has {len(outputs)} output_files, expected 1"
        out = outputs[0]
        assert out["placeholder"] == "output_path"
        assert out["filename_placeholder"] == "output_filename"


def test_output_suffix_matches_route(convert_endpoints):
    """The output_files.suffix becomes the file extension on
    the generated download URL. It must match the route's
    target format so consumers downloading from /to/pdf get
    a file ending in ``.pdf``."""
    for e in convert_endpoints:
        route_format = e["route"].rsplit("/", 1)[-1]
        suffix = e["output_files"][0]["suffix"]
        assert suffix == f".{route_format}", \
            f"{e['name']} suffix {suffix!r} != .{route_format}"


def test_output_placeholder_is_substituted(convert_endpoints):
    """The output path is materialized by url2code before
    the wrapper runs. Without the placeholder the wrapper
    would try to write to the literal string."""
    for e in convert_endpoints:
        assert "{output_path}" in e["command"]["args"], \
            f"{e['name']} missing {{output_path}} arg"


# ---------------------------------------------------------------------------
# output mode — text, because the wrapper's stdout is informational
# ---------------------------------------------------------------------------


def test_every_convert_endpoint_uses_text_output_mode(convert_endpoints):
    """The wrapper writes a single ``wrote <path>`` line on
    success and surfaces soffice errors on failure. There's
    no structured output for url2code to parse — text mode
    captures the line and trusts the exit code; the
    download_url in the JSON response is the actual
    deliverable. (/formats uses native_json and is
    excluded.)"""
    for e in convert_endpoints:
        assert e["output"]["mode"] == "text", \
            f"{e['name']} must use text output mode"


# ---------------------------------------------------------------------------
# formats.yaml -- the canonical catalog
# ---------------------------------------------------------------------------


def test_catalog_parses(catalog):
    """formats.yaml must be a list of {ext, name, family}
    objects. Anything else and the YAML <-> catalog
    consistency tests above silently break."""
    assert isinstance(catalog, list)
    assert len(catalog) >= 1
    required = {"ext", "name", "family"}
    for entry in catalog:
        assert isinstance(entry, dict)
        assert required <= set(entry.keys()), \
            f"entry {entry!r} missing keys {required - set(entry.keys())}"


def test_catalog_exts_are_unique(catalog):
    exts = [e["ext"] for e in catalog]
    assert len(exts) == len(set(exts)), \
        f"duplicate exts: {sorted(exts)}"


def test_catalog_exts_are_url_safe(catalog):
    """ext becomes the trailing path component of /to/<ext>.
    Letters and digits only; not empty."""
    import re
    pattern = re.compile(r"^[a-z0-9]+$")
    for entry in catalog:
        assert pattern.match(entry["ext"]), \
            f"ext {entry['ext']!r} is not URL-safe"


def test_catalog_family_is_known(catalog):
    """family drives per-family timeouts in tools.yaml and
    documentation grouping. Limit to the documented set."""
    for entry in catalog:
        assert entry["family"] in VALID_FAMILIES, \
            f"ext {entry['ext']!r} has unknown family " \
            f"{entry['family']!r}"


# ---------------------------------------------------------------------------
# /formats -- discovery endpoint
# ---------------------------------------------------------------------------


def test_formats_endpoint_is_get(endpoints):
    """Discovery is parameter-less and read-only -- GET is
    the honest verb. POST is the default in url2code for
    tool invocations; this is the exception."""
    e = next(e for e in endpoints if e["name"] == "formats")
    assert e["method"] == "GET"


def test_formats_endpoint_returns_native_json(endpoints):
    """/formats wraps cat-yaml-as-json over the catalog so
    the response's `parsed_output` is the catalog directly.
    text mode would force callers to re-parse."""
    e = next(e for e in endpoints if e["name"] == "formats")
    assert e["output"]["mode"] == "native_json"


def test_formats_endpoint_reads_catalog_file(endpoints):
    """The endpoint runs cat-yaml-as-json on the YAML
    catalog. Pinning the wrapper path also catches a stray
    refactor that switches to /bin/cat (which would emit
    raw YAML and break native_json parsing)."""
    e = next(e for e in endpoints if e["name"] == "formats")
    assert e["command"]["executable"] == "/app/bin/cat-yaml-as-json"
    assert e["command"]["args"] == ["/app/config/formats.yaml"]

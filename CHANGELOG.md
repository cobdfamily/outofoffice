# Changelog

All notable changes to outofoffice. Format roughly follows
[Keep a Changelog](https://keepachangelog.com); dates
are ISO 8601 in UTC.

Pre-existing release tags (if any) are still visible
via `git log --tags --oneline`; this file starts
empty and is filled forward from this point.

## [Unreleased]

## [1.1.0] - 2026-06-01

### Added
- **PDF/A archival output.** `POST /to/pdf` accepts `pdfa=yes`,
  which makes the `soffice-convert` wrapper render via
  LibreOffice's `writer_pdf_Export` FilterData with
  `SelectPdfVersion=1` (PDF/A-1b) — a self-contained, validator-
  friendly archival PDF — instead of a plain PDF. Default
  (omitted / `pdfa=no`) is unchanged. New e2e test asserts the
  `pdfaid` XMP marker. First of the "export profile" knobs;
  JPEG quality / raster DPI remain follow-ups (DPI needs
  pixel-dimension math, not a direct LibreOffice option).

### Changed
- `api.version` `1.0.0 -> 1.1.0`.

## [1.0.0] - 2026-06-01

First tagged release of outofoffice. Captures the existing
surface plus this sprint's standardization work.

### Added
- Document conversion over HTTP via LibreOffice headless
  (`soffice`): one `POST /to/<ext>` route per supported target
  format (42 formats) plus a `/formats` discovery endpoint.
  Source format auto-detected from the upload. Full surface in
  README.md.
- `api.version "1.0.0"` on `GET /` liveness (Sprint 1).
- Daily Grype CVE scan (`.github/workflows/cve-scan.yml`) over
  the image's oras-attached CycloneDX SBOM (Sprint 4).

### Changed
- Pinned the url2code base image to `1.0.8` (was `latest`) for
  reproducible builds (Sprint 1).
- Hardened `docker-compose.yaml`: read-only root, tmpfs `/tmp`,
  `cap_drop: ALL`, `no-new-privileges`, and `HOME=/tmp` so
  LibreOffice's caches stay on the writable tmpfs (Sprint 4).

[1.1.0]: https://github.com/cobdfamily/outofoffice/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/cobdfamily/outofoffice/commits/v1.0.0

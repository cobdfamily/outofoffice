# outofoffice

[![test](https://github.com/cobdfamily/outofoffice/actions/workflows/test.yml/badge.svg)](https://github.com/cobdfamily/outofoffice/actions/workflows/test.yml)

A simple document conversion API.

This is a YAML-defined microservice — no Python source in
the repo, only tests. The HTTP surface lives in
[`config/tools.yaml`](config/tools.yaml) and is consumed
by the upstream `cobdfamily/url2code` engine, which
outofoffice's image is built on top of.

## What it does

One route per supported target format, all under
`/to/<ext>`, plus a discovery endpoint:

```
POST /to/<ext>
   body:    multipart/form-data, field `document` = the input file
   returns: JSON with output_files.output_path.download_url
            -> GET that URL to retrieve the converted bytes

GET /formats
   Return the curated catalog of supported export formats.
   Use the `ext` field of any entry as the path suffix on
   /to/<ext>.
```

The source format is detected by LibreOffice from the
upload's bytes / extension; you don't declare it in the
route. Cross-application conversions (e.g. `.docx ->
.xlsx`) will fail at LibreOffice with a non-zero exit;
common-sense applies to which input/output pairs make
sense.

### Supported target formats

The catalog lives in
[`config/formats.yaml`](config/formats.yaml) and is served
at `/formats` (converted to JSON on the wire by
`bin/cat-yaml-as-json`). 42 entries, grouped by family:

- **Word processing / text** (12): `pdf`, `docx`, `doc`,
  `odt`, `fodt`, `ott`, `rtf`, `txt`, `html`, `xhtml`,
  `epub`, `uot`
- **Spreadsheet** (9): `xlsx`, `xls`, `ods`, `fods`,
  `ots`, `csv`, `dbf`, `dif`, `slk`
- **Presentation** (5): `pptx`, `ppt`, `odp`, `fodp`,
  `otp`
- **Drawing / vector** (6): `odg`, `fodg`, `otg`, `svg`,
  `emf`, `wmf`
- **Raster image** (6): `png`, `jpg`, `gif`, `bmp`,
  `tiff`, `webp`
- **Markup / other** (4): `mml`, `ps`, `eps`, `ltx`

The full surface is enumerated in
[`config/tools.yaml`](config/tools.yaml). Every conversion
endpoint has the same shape: one `document` upload, one
converted output file, a download URL on the response.

Adding a format is a `formats.yaml` edit + a
`tools.yaml` edit (one new endpoint block, copying any
existing one). CI fails fast if the two drift.

## Quick start

```sh
docker compose up -d

# Convert a Word doc to PDF.
curl -X POST -F document=@./report.docx \
     http://localhost:8000/to/pdf | jq

# Response (abbreviated):
# {
#   "exit_code": 0,
#   "output": "wrote /tmp/outofoffice/outputs/<random>.pdf\n",
#   "output_files": {
#     "output_path": {
#       "filename":     "<random>.pdf",
#       "download_url": "/<...>/output_path/<random>.pdf"
#     }
#   }
# }

# Fetch the converted file.
curl -OJ http://localhost:8000/<download_url-from-above>
```

## How conversions work

The container runs `libreoffice` (the `soffice` CLI)
headlessly. Each request:

1. url2code receives the multipart upload, writes it to
   `/tmp/outofoffice/uploads/<random>.<orig-ext>`.
2. url2code generates a randomized output path
   `/tmp/outofoffice/outputs/<random>.<target-ext>`.
3. url2code invokes
   `/app/bin/soffice-convert <input> <output> <fmt>`.
4. The wrapper runs soffice into a per-invocation temp
   dir (so concurrent requests don't fight over the user
   profile) and renames the result to the path url2code
   expected.
5. url2code responds with JSON containing a
   `download_url` for the converted bytes; the file is
   served via FastAPI's `FileResponse` and cleaned up
   when the operator restarts the container.

## What it doesn't do

- **No auth**. Gate the service at your reverse proxy
  (Traefik / nginx) — see DEPLOYMENT.md.
- **No persistence**. Uploads and converted outputs live
  in `/tmp` and are wiped on container restart. Caching
  / archiving converted files is the caller's
  responsibility.
- **No format negotiation**. The route picks the target;
  the source is whatever LibreOffice can open.
- **Not a quality knob**. PDF export uses LibreOffice's
  defaults. Operators with bespoke export options should
  build a downstream image that ships their own
  `soffice-convert` wrapper.

## Files

```
config/tools.yaml             # the entire HTTP surface
config/formats.yaml           # canonical format catalog
bin/soffice-convert           # shell wrapper (3 quirks of soffice)
bin/cat-yaml-as-json          # YAML -> JSON for /formats
Dockerfile                    # url2code base + LibreOffice
docker-compose.yaml           # local-dev / production-shape compose
tests/test_config.py          # YAML + catalog structural tests
tests/test_e2e.py             # docker-compose round-trip tests
.github/workflows/test.yml    # CI: yaml + e2e jobs (+ nightly)
.github/workflows/release.yml # CI: tag-driven multi-arch build/push
```

# Deployment

outofoffice ships as a container image to the kibble
registry on every `git tag v*`. The image is built on top
of `cobdfamily/url2code:<tag>` and adds:

- `libreoffice` (`apt-get`)
- `fonts-liberation` + `fonts-dejavu-core` for baseline
  font coverage
- `config/tools.yaml` — the entire HTTP surface
- `bin/soffice-convert` — a small shell wrapper that
  bridges three soffice quirks (output filename naming,
  per-invocation user-profile dir, stderr→stdout)

No Python source is added; the runtime is url2code's
FastAPI engine, configured by the YAML.

## Pre-flight checklist

- [ ] Public hostname for outofoffice (eg.
      `convert.cobd.ca` or
      `outofoffice.openapis.ca`) with an A record. The
      service speaks plain HTTP on `:8000` behind your
      reverse proxy / TLS terminator.
- [ ] Disk space on `/tmp` for uploads + converted
      outputs. Each request writes the input under
      `/tmp/outofoffice/uploads` and the output under
      `/tmp/outofoffice/outputs`. Both are wiped on
      container restart; sustained throughput needs
      enough headroom for in-flight requests.
- [ ] Memory for LibreOffice's runtime — a single
      conversion peaks around 200-400 MB; serial
      conversions reuse the same RAM, parallel ones
      multiply.

## Image distribution

`.github/workflows/release.yml` builds and pushes the
image on every `git tag v*`:

```sh
git tag -a v0.1.0 -m "Release 0.1.0"
git push origin v0.1.0
```

Within a couple of minutes:

- `kibble.apps.blindhub.ca/cobdfamily/outofoffice:0.1.0`
- `kibble.apps.blindhub.ca/cobdfamily/outofoffice:latest`

Multi-arch (amd64 + arm64), matching the fleet.

The image is large — LibreOffice + fonts adds ~700-900 MB
on top of the url2code base. Expect proportionally bigger
pulls and slower first-boot than the other YAML-driven
microservices.

## No built-in auth

Every `/to/<ext>` endpoint is unauthenticated by default.
Gate the service at your reverse proxy if you don't want
the conversion API open to the world. Sample nginx
snippet:

```nginx
location / {
    if ($http_x_api_key != "$OUTOFOFFICE_API_KEY") {
        return 401;
    }
    # File uploads can be large — bump the body limit.
    client_max_body_size 50m;
    proxy_pass http://127.0.0.1:8000;
    proxy_read_timeout 300s;  # cold-start LibreOffice is slow
}
```

For the openapis.ca marketplace shape, see
`infra/docs/auth-strategy.md` in the workspace root.

## Run

```yaml
# /opt/outofoffice/docker-compose.yaml
services:
  outofoffice:
    image: kibble.apps.blindhub.ca/cobdfamily/outofoffice:0.1.0
    container_name: outofoffice
    restart: unless-stopped
    ports:
      - "127.0.0.1:8000:8000"
    # Optional: bump shared-memory size if you'll be
    # converting large multi-page documents. LibreOffice
    # uses /dev/shm for inter-process scratch.
    # shm_size: "1g"
```

```sh
mkdir -p /opt/outofoffice
cd /opt/outofoffice
docker compose pull
docker compose up -d
docker compose logs -f outofoffice
```

Behind your TLS reverse proxy, route
`https://convert.cobd.ca/*` to `127.0.0.1:8000`.

## Verify

```sh
# Liveness — returns service / status / version:
curl -fsS https://convert.cobd.ca/

# Generated OpenAPI docs at /docs and /redocs.

# Convert a Word document to PDF:
curl -fsS -X POST \
  -F document=@./report.docx \
  https://convert.cobd.ca/to/pdf | jq
# Take the download_url from the response and GET it.

# Convert a spreadsheet to CSV:
curl -fsS -X POST \
  -F document=@./quarterly.xlsx \
  https://convert.cobd.ca/to/csv | jq
```

## Routine operations

### Upgrading

```sh
git tag -a v0.1.1 -m "Release 0.1.1"
git push origin v0.1.1
# CI builds and pushes.

sed -i 's|outofoffice:[^ ]*|outofoffice:0.1.1|' docker-compose.yaml
docker compose pull
docker compose up -d --no-deps outofoffice
```

### Adding a target format

1. Add the format's extension to `EXPECTED_FORMATS` in
   `tests/test_config.py`.
2. Add the matching endpoint to `config/tools.yaml`
   (copy any existing block, change the route, suffix,
   and the third command arg).
3. `pytest tests/test_config.py` to confirm.
4. Tag a release.

The bin/soffice-convert wrapper is format-agnostic — it
forwards whatever extension you pass to LibreOffice's
`--convert-to`. If LibreOffice supports it as an export
filter, no wrapper changes are needed.

### Adding fonts

LibreOffice renders documents using the fonts available
in the container. The base image ships `fonts-liberation`
(metric-compatible Arial / Times / Courier) and
`fonts-dejavu-core` (broad Unicode coverage). To add
more, build a downstream image:

```Dockerfile
FROM kibble.apps.blindhub.ca/cobdfamily/outofoffice:0.1.0
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
 && rm -rf /var/lib/apt/lists/*
USER url2code
```

### Backups

There is **nothing** to back up. outofoffice is
stateless — uploads and outputs live in `/tmp` and are
wiped on container restart. Consumers persist the
converted bytes; the service does not.

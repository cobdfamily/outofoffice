# outofoffice image: cobdfamily/url2code base + LibreOffice.
#
# No Python source in this repo (tests aside). The HTTP surface
# is entirely defined in config/tools.yaml — url2code reads it
# on startup and registers the FastAPI routes from it.
#
# One route per supported target format under /to/<format>;
# the upload is multipart field ``document``. Each route
# returns JSON containing a download URL for the converted
# output -- url2code's built-in output_files mechanism.

ARG URL2CODE_TAG=latest
FROM kibble.apps.blindhub.ca/cobdfamily/url2code:${URL2CODE_TAG}

USER root

# LibreOffice is the converter; the apt package pulls in the
# headless components we actually use plus its document
# filters. ``--no-install-recommends`` skips the desktop
# integration packages and trims ~250 MB.
#
# fonts-liberation gives LibreOffice a baseline of metric-
# compatible fonts so most Arial / Times documents render
# without missing-glyph blocks. fonts-dejavu-core is a
# broader Unicode fallback. Operators who need additional
# typefaces can layer them in a downstream image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        libreoffice \
        fonts-liberation \
        fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

# Pre-create the temp tree as root + chown to the runtime
# user so the upload write and the converted-output write
# both succeed without further chowns at request time.
RUN mkdir -p /tmp/outofoffice/uploads /tmp/outofoffice/outputs \
 && chown -R url2code:url2code /tmp/outofoffice

# Replace url2code's bundled example tools.yaml with
# outofoffice's. The base image's URL2CODE_CONFIG defaults
# to /app/config/tools.yaml.
COPY --chown=url2code:url2code config /app/config

# Wrapper scripts:
#   bin/soffice-convert     bridges three soffice / url2code
#                           quirks (see header comment).
# cat-yaml-as-json is provided by url2code:>=1.0.7 itself
# (lives at /app/bin/cat-yaml-as-json in the base layer);
# this image's bin/ COPY layers on top without clobbering
# it. Used by the /v1/formats discovery endpoint.
COPY --chown=url2code:url2code bin /app/bin
RUN chmod 0755 /app/bin/soffice-convert

USER url2code

# CMD inherited from the base image
# (uvicorn url2code.main:app --host 0.0.0.0 --port 8000) is
# preserved; ENTRYPOINT is unset.

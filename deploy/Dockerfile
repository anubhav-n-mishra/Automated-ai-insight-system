# syntax=docker/dockerfile:1.7
#
# Multi-stage build.
#
# The builder installs into a virtual environment that is copied wholesale into
# a clean runtime stage, so compilers and build headers never reach the shipped
# image. The runtime runs as an unprivileged user on a read-only root
# filesystem with a single writable volume, because a report generator has no
# reason to be able to modify its own code.

# --------------------------------------------------------------- builder ----
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy the metadata first so dependency resolution is cached independently of
# source edits.
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src

ARG EXTRAS=""
RUN pip install --upgrade pip setuptools wheel \
    && pip install ".${EXTRAS}"

# --------------------------------------------------------------- runtime ----
FROM python:3.12-slim-bookworm AS runtime

ARG VERSION=dev
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="Insight Engine" \
      org.opencontainers.image.description="Config-driven, explainable period-over-period analytics reporting." \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/anubhav-n-mishra/Automated-ai-insight-system"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    INSIGHT_ENGINE_HOST=0.0.0.0 \
    INSIGHT_ENGINE_PORT=8000 \
    INSIGHT_ENGINE_DATA_DIR=/var/lib/insight-engine \
    INSIGHT_ENGINE_LOG_FORMAT=json

RUN groupadd --system --gid 10001 insight \
    && useradd --system --uid 10001 --gid insight --no-create-home --shell /usr/sbin/nologin insight

COPY --from=builder /opt/venv /opt/venv

# The only writable path. Mount a volume here in production: everything in it
# (decks, audio, sessions, uploads) is regenerable but not free to lose.
RUN mkdir -p /var/lib/insight-engine \
    && chown -R insight:insight /var/lib/insight-engine

USER insight:insight
WORKDIR /var/lib/insight-engine
EXPOSE 8000
VOLUME ["/var/lib/insight-engine"]

# Uses the readiness endpoint, not the liveness one: an instance that cannot
# write its data directory is up but useless, and should be pulled from rotation.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import sys,urllib.request;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=4).status==200 else 1)"

ENTRYPOINT ["insight-engine"]
CMD ["serve"]

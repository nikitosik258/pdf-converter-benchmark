# syntax=docker/dockerfile:1.7
# CPU image: classic local converters and optional cloud API clients.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PDF_BENCHMARK_WEB_PROJECT_ROOT=/opt/pdf-benchmark \
    PDF_BENCHMARK_WEB_STORAGE_ROOT=/var/lib/pdf-benchmark/runtime \
    PDF_BENCHMARK_WEB_HOST=0.0.0.0 \
    PDF_BENCHMARK_WEB_PORT=8000

WORKDIR /opt/pdf-benchmark

RUN apt-get update \
    && apt-get install --yes --no-install-recommends curl libglib2.0-0 libgomp1 libgl1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin app

COPY pyproject.toml ./
COPY requirements-web-pinned.txt ./
RUN python -m venv .venv \
    && .venv/bin/pip install --upgrade pip \
    && .venv/bin/pip install -r requirements-web-pinned.txt

COPY src ./src
COPY scripts/benchmark_worker.py ./scripts/benchmark_worker.py
COPY config ./config

# The worker deliberately keeps its historical isolated environments.  The
# main environment contains only lightweight CPU tools; cloud SDKs live in a
# separate environment and become usable only with server-side credentials.
RUN .venv/bin/pip install --no-deps ".[web,pymupdf,pdfplumber,pdfminer]" \
    && python -m venv .venv-cloud \
    && .venv-cloud/bin/pip install --upgrade pip \
    && .venv-cloud/bin/pip install ".[cloud-all]" \
    && mkdir -p /var/lib/pdf-benchmark/runtime /home/app/.cache \
    && chown -R app:app /var/lib/pdf-benchmark /home/app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl --fail --silent http://127.0.0.1:8000/api/v1/health || exit 1

CMD ["/opt/pdf-benchmark/.venv/bin/pdf-benchmark-api"]

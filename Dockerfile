# Hosted Stellis Health: the account gateway and one workspace process per account, data on a volume.

# The live recording preview's recognizer. sherpa-onnx links its prebuilt static libraries at build
# time; the builder matches the runtime's Debian release so the binary finds the same glibc and libstdc++.
FROM rust:1.90-slim-trixie AS audio-worker
RUN apt-get update && apt-get install -y --no-install-recommends g++ && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY rust/stellis-audio-worker ./
RUN cargo build --release --locked

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-eng && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.11.32

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies first, so code changes reuse this layer.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-install-project

COPY LICENSE NOTICE ./
COPY src ./src
RUN uv sync --locked

ENV PATH=/app/.venv/bin:$PATH
COPY --from=audio-worker /build/target/release/stellis-audio-worker /usr/local/bin/stellis-audio-worker
EXPOSE 8080

# The app runs as this unprivileged user; the entrypoint gives it the data volume.
RUN useradd --system --create-home --uid 10001 stellis
COPY scripts/check_ocr.py /usr/local/libexec/stellis/check_ocr.py
# Exercise the bounded worker and English data as the runtime user before shipping.
RUN setpriv --reuid=stellis --regid=stellis --init-groups \
    python /usr/local/libexec/stellis/check_ocr.py
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["docker-entrypoint.sh"]

# Speech models download to $STELLIS_DATA_DIR/models on first start: about 500 MB for final
# transcripts, then about 1.3 GB for the live preview.
CMD ["stellis", "serve", "--port", "8080"]

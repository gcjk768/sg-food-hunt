# SG Food Hunt: weekly collector + Telegram notifier for a NAS (Synology / QNAP / any Docker host).
# Build:  docker compose build
# Run:    docker compose up -d        (runs on the schedule in SGFH_SCHEDULE, see docker-compose.yml)
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Singapore

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY sgfoodhunt ./sgfoodhunt
RUN pip install .

# Runtime state lives in mounted volumes (see docker-compose.yml), never in the image.
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
    && mkdir -p /app/config /app/data /app/logs /app/vault /app/social_exports

VOLUME ["/app/config", "/app/data", "/app/logs", "/app/vault", "/app/social_exports"]
HEALTHCHECK --interval=5m --timeout=20s CMD sgfh doctor -C /app/config --quiet || exit 1
ENTRYPOINT ["/entrypoint.sh"]
CMD ["serve"]

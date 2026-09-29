# SG Food Hunt: weekly collector + Telegram notifier for a NAS (Synology / QNAP / any Docker host).
# Build:  docker compose build
# Run:    docker compose up -d        (runs on the schedule in SGFH_SCHEDULE, see docker-compose.yml)
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Singapore \
    CLAUDE_CONFIG_DIR=/app/claude-config

# WITH_CLAUDE=true installs Node and the Claude Code CLI so the optional AI layer (`ai.enabled`)
# can shell out to `claude -p`. Authenticate with ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN in .env.
ARG WITH_CLAUDE=true
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates curl gnupg \
    && if [ "$WITH_CLAUDE" = "true" ]; then \
         curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
         && apt-get install -y --no-install-recommends nodejs \
         && npm install -g @anthropic-ai/claude-code; \
       fi \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY sgfoodhunt ./sgfoodhunt
RUN pip install .

# Runtime state lives in mounted volumes (see docker-compose.yml), never in the image.
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
    && mkdir -p /app/config /app/data /app/logs /app/vault /app/social_exports /app/claude-config

VOLUME ["/app/config", "/app/data", "/app/logs", "/app/vault", "/app/social_exports", "/app/claude-config"]
HEALTHCHECK --interval=5m --timeout=20s CMD sgfh doctor -C /app/config --quiet || exit 1
ENTRYPOINT ["/entrypoint.sh"]
CMD ["serve"]

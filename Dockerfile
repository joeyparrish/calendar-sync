# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /bin/uv
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY README.md LICENSE.md ./
COPY src ./src
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
USER nobody
CMD exec gunicorn --bind ":${PORT:-8080}" --workers 1 --threads 8 --timeout 300 --no-control-socket "calendar_sync.main:create_app_from_env()"

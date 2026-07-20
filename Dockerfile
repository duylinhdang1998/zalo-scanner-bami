# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────────────────
# Zalo Bot Scanner — multi-stage Dockerfile
# Base: python:3.11-slim (pinned major.minor — không dùng latest)
#
# Stage 1 (builder): cài Python packages vào /install
# Stage 2 (runtime): image gọn, non-root user, chỉ copy artifact cần thiết
# ─────────────────────────────────────────────────────────────────────────────

# ─── Stage 1: builder ────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# psycopg[binary] dùng binary wheel (đã bundle libpq) → không cần gcc/libpq-dev
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ─── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Non-root user (principle of least privilege)
RUN addgroup --system botgrp \
    && adduser --system --ingroup botgrp --no-create-home botuser

# Copy installed packages từ stage builder vào /usr/local
# → alembic, python-zalo-bot, httpx, SQLAlchemy, psycopg, etc.
COPY --from=builder /install /usr/local

WORKDIR /app

# Copy source code cần thiết cho bot
# Thứ tự: dep ít thay đổi trước → tận dụng Docker layer cache
COPY alembic.ini     ./alembic.ini
COPY alembic/        ./alembic/
COPY config/         ./config/
COPY db/             ./db/
COPY nlq/            ./nlq/
COPY vision/         ./vision/
COPY bot/            ./bot/
COPY main.py         ./main.py

# Entrypoint script
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Đổi quyền sở hữu thư mục app cho non-root user
RUN chown -R botuser:botgrp /app

USER botuser

# Healthcheck: bot long-polling nên không có HTTP server.
# Kiểm tra process python main.py còn sống.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD pgrep -f "python main.py" > /dev/null || exit 1

ENTRYPOINT ["/entrypoint.sh"]

#!/bin/sh
# docker/entrypoint.sh — Entrypoint cho Zalo Bot Scanner container
#
# Logic:
#   1. Chạy Alembic migration (dùng DATABASE_MIGRATION_URL nếu có, else DATABASE_URL)
#      → Fail-fast: nếu migrate lỗi thì container dừng ngay (set -e)
#   2. Start bot: exec python main.py (thay thế PID 1 — nhận SIGTERM đúng cách)
set -e

echo "[entrypoint] ── Zalo Bot Scanner ──────────────────────────────"
echo "[entrypoint] DB migration URL: ${DATABASE_MIGRATION_URL:+set (DATABASE_MIGRATION_URL)}${DATABASE_MIGRATION_URL:-using DATABASE_URL}"

# Alembic env.py đọc DATABASE_MIGRATION_URL (ưu tiên) hoặc DATABASE_URL để migrate.
# Đây là cổng DIRECT 5432 của Supabase — bắt buộc với cổng pooled PgBouncer.
echo "[entrypoint] Running: alembic upgrade head ..."
alembic upgrade head

echo "[entrypoint] Migration complete."
echo "[entrypoint] Starting bot: python main.py"

# exec thay thế shell process → SIGTERM/SIGINT truyền thẳng vào Python
exec python main.py

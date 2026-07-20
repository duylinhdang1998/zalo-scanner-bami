#!/usr/bin/env bash
# Chạy Zalo Bot Scanner — 1 lệnh, tự lo venv + kiểm tra .env.
# Dùng:  ./run.sh          (chạy bot)
#        ./run.sh extract <ảnh>   (test đọc 1 ảnh, không cần Zalo)
set -e
cd "$(dirname "$0")"

# 1) Tạo venv nếu chưa có + cài dependency
if [ ! -x .venv/bin/python ]; then
  echo "→ Tạo môi trường ảo (.venv) lần đầu…"
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
fi

PY=.venv/bin/python

# 2) Kiểm tra .env
if [ ! -f .env ]; then
  cp .env.example .env
  echo "⚠️  Đã tạo .env — mở file này điền ZALO_BOT_TOKEN và BEEKNOEE_API_KEY rồi chạy lại."
  exit 1
fi

# 3) Chế độ test đọc ảnh: ./run.sh extract <đường-dẫn-ảnh>
if [ "$1" = "extract" ]; then
  shift
  exec "$PY" -m scripts.test_extract "$@"
fi

# 4) Chạy bot (long polling)
# Mặc định dùng data/live.db (dữ liệu BỀN, không bị test xoá). Không ghi đè nếu bạn đã set sẵn.
export DATABASE_URL="${DATABASE_URL:-sqlite:///data/live.db}"
echo "→ Khởi động Zalo Bot… (DB: $DATABASE_URL) — Ctrl+C để dừng"
exec "$PY" main.py

"""Điểm khởi chạy Zalo Bot Scanner."""
from __future__ import annotations

import logging
from urllib.parse import urlparse, urlunparse

from bot.app import build_application
from config.settings import settings
from db.database import init_db


def _redact_url(url: str) -> str:
    """Che mật khẩu trong URL trước khi ghi log.

    Ví dụ:
        postgresql+psycopg://user:secret@host:5432/db
        → postgresql+psycopg://*:*@host:5432/db

    URL không có credentials (SQLite, v.v.) trả về nguyên bản.
    """
    parsed = urlparse(url)
    if not (parsed.username or parsed.password):
        return url  # không có credentials — trả nguyên bản, tránh lỗi urlunparse
    netloc = f"*:*@{parsed.hostname or ''}"
    if parsed.port:
        netloc += f":{parsed.port}"
    parsed = parsed._replace(netloc=netloc)
    return urlunparse(parsed)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    # httpx log cả URL chứa token vào mỗi request → hạ xuống WARNING để không lộ token + gọn log
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings.require()
    init_db()

    app = build_application()
    logging.getLogger("bot").info(
        "Bot khởi động | vision=%s | db=%s | scan_mode=%s",
        settings.vision_model,
        _redact_url(settings.database_url),
        settings.scan_mode,
    )
    app.run_polling()


if __name__ == "__main__":
    main()

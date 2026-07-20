"""Khởi tạo engine + session. Mặc định SQLite; đổi DATABASE_URL để dùng Postgres/Supabase.

Supabase có 2 cổng:
  - 6543 (pooled/PgBouncer): dùng cho app runtime → DATABASE_URL
  - 5432 (direct):           dùng cho Alembic migration → DATABASE_MIGRATION_URL
    Ví dụ: postgresql+psycopg://user:pass@db.xxx.supabase.co:5432/postgres

Biến môi trường bổ sung (đọc tại đây với default an toàn):
  DB_POOL_SIZE     — số kết nối trong pool (default: 5)
  DB_MAX_OVERFLOW  — kết nối tăng thêm khi pool đầy (default: 10)
  DB_POOL_TIMEOUT  — giây chờ lấy kết nối (default: 30)
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from config.settings import settings
from db.models import Base

_url = settings.database_url
_is_sqlite = _url.startswith("sqlite")

# Đảm bảo thư mục cho SQLite tồn tại
if _is_sqlite:
    db_path = _url.split("///", 1)[-1]
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)


def _make_engine():
    if _is_sqlite:
        return create_engine(
            _url,
            echo=False,
            connect_args={"check_same_thread": False},
        )

    # Postgres / Supabase
    pool_size = int(os.getenv("DB_POOL_SIZE", "5"))
    max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "10"))
    pool_timeout = int(os.getenv("DB_POOL_TIMEOUT", "30"))

    connect_args: dict = {}
    # psycopg3 + PgBouncer transaction pooler (Supabase cổng 6543): psycopg3
    # tự tạo prepared statement ("_pg3_0"), nhưng PgBouncer tái dùng connection
    # giữa các client → lỗi DuplicatePreparedStatement (ngắt quãng khi restart).
    # prepare_threshold=None → KHÔNG dùng server-side prepared statement → hết lỗi.
    _is_psycopg3 = "+psycopg" in _url and "+psycopg2" not in _url
    if _is_psycopg3:
        connect_args["prepare_threshold"] = None

    return create_engine(
        _url,
        echo=False,
        pool_pre_ping=True,          # phát hiện kết nối chết trước khi dùng
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        connect_args=connect_args,
    )


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    """Tạo bảng nếu chưa có — CHỈ cho dev/SQLite.

    Production Postgres/Supabase: schema do `alembic upgrade head` quản lý
    (entrypoint chạy trước khi start bot). Bỏ qua create_all ở đây để không
    phát sinh truy vấn reflection thừa qua PgBouncer lúc khởi động.
    """
    if _is_sqlite:
        Base.metadata.create_all(engine)


@contextmanager
def get_session() -> Session:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

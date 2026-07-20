"""Alembic environment — trỏ vào settings.database_url và Base.metadata.

Cách chạy:
  # Áp migration lên DB đang cấu hình:
  alembic upgrade head

  # Với Supabase, dùng cổng direct 5432 cho migration:
  DATABASE_URL="postgresql+psycopg://user:pass@db.xxx.supabase.co:5432/postgres" alembic upgrade head

  # Sinh revision mới (autogenerate từ models):
  alembic revision --autogenerate -m "tên thay đổi"
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Thêm thư mục gốc vào sys.path để import được config + db
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import settings  # noqa: E402
from db.models import Base  # noqa: E402

# Alembic Config object
config = context.config

# Setup logging từ alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata của các model — hỗ trợ autogenerate
target_metadata = Base.metadata

# Override sqlalchemy.url từ settings (hoặc biến môi trường DATABASE_URL)
# Với Supabase migration nên dùng DATABASE_MIGRATION_URL (cổng 5432 direct)
migration_url = os.getenv("DATABASE_MIGRATION_URL") or settings.database_url
config.set_main_option("sqlalchemy.url", migration_url)


def run_migrations_offline() -> None:
    """Chạy migration ở chế độ offline (sinh SQL script, không cần kết nối DB)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Chạy migration ở chế độ online (kết nối DB thật)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

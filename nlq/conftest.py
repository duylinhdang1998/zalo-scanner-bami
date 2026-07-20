"""pytest fixtures shared across nlq/ tests.

Mirrors tests/conftest.py but scoped to nlq/ directory so tests here
can use fresh_db without the global autouse fixtures from tests/.
"""
from __future__ import annotations

import os

# ── Env overrides (before any project import) ─────────────────────────────────
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("BEEKNOEE_API_KEY", "test-key-for-tests")
os.environ.setdefault("ZALO_BOT_TOKEN", "test-zalo-token")
os.environ.setdefault("BEEKNOEE_BASE_URL", "https://example-test.com/v1")
os.environ.setdefault("ZALO_IMAGE_HOST_ALLOWLIST", "zadn.vn,zaloapp.com,zalo.me,zdn.vn")
os.environ.setdefault("SCAN_MODE", "mention")
os.environ.setdefault("CONFIRM_THRESHOLD", "0.6")

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """Fresh SQLite file DB per test — full isolation.

    Patches db.repository.get_session so all repository calls use the test DB.
    Returns the bound sessionmaker for ad-hoc queries in tests.
    """
    from db.models import Base
    import db.database
    import db.repository

    db_url = f"sqlite:///{tmp_path}/test.db"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def _get_session():
        s = Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    monkeypatch.setattr(db.database, "get_session", _get_session)
    monkeypatch.setattr(db.repository, "get_session", _get_session)
    return Session

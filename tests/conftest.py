"""pytest configuration and shared fixtures.

All environment variables MUST be set before any project module is imported,
because config/settings.py and db/database.py read them at module level.
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

# ── Standard imports (after env setup) ────────────────────────────────────────
from collections import defaultdict, deque
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


# ── DB fixture ────────────────────────────────────────────────────────────────

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


# ── Rate-limit isolation ───────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_rate_buckets(monkeypatch):
    """Ensure rate-limit bucket state is clean for every test."""
    import bot.handlers
    monkeypatch.setattr(bot.handlers, "_rate_buckets", defaultdict(deque))


# ── Fake Zalo SDK objects ─────────────────────────────────────────────────────

class FakeUser:
    def __init__(self, uid: str = "user123", name: str = "Test User"):
        self.id = uid
        self.display_name = name
        self.account_name = None


class FakeChat:
    def __init__(self, cid: str = "group456", is_group: bool = True):
        self.id = cid
        self.type = "group" if is_group else "private"


class FakeMessage:
    def __init__(
        self,
        text: str = "",
        photo_url: str | None = None,
        is_group: bool = True,
        uid: str = "user123",
        name: str = "Test User",
        cid: str = "group456",
    ):
        self.chat = FakeChat(cid=cid, is_group=is_group)
        self.from_user = FakeUser(uid=uid, name=name)
        self.text = text
        self.photo_url = photo_url
        self.sticker = None
        self.sticker_id = None
        self.type = "photo" if photo_url else "text"
        self._replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self._replies.append(text)


class FakeUpdate:
    def __init__(
        self,
        text: str = "",
        photo_url: str | None = None,
        is_group: bool = True,
        uid: str = "user123",
        name: str = "Test User",
        cid: str = "group456",
    ):
        self.message = FakeMessage(
            text=text,
            photo_url=photo_url,
            is_group=is_group,
            uid=uid,
            name=name,
            cid=cid,
        )

    @property
    def replies(self) -> list[str]:
        return self.message._replies


class FakeContext:
    """Minimal fake for PTB context."""
    pass

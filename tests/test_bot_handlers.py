"""Tests for bot/handlers.py — _should_scan, _check_rate_limit, on_photo flows.

All async functions tested via pytest-asyncio (asyncio_mode=auto in pytest.ini).
photo_bytes() is mocked; extract_document() is mocked; save_extraction_v2() is mocked.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot.handlers as handlers
from db.repository import SaveResult
from db.models import Document
from tests.conftest import FakeContext, FakeUpdate


# ── _should_scan ──────────────────────────────────────────────────────────────

class TestShouldScan:
    def _update(self, text="", is_group=True):
        return FakeUpdate(text=text, is_group=is_group)

    def test_auto_mode_always_true(self, monkeypatch):
        from config import settings as settings_mod
        from dataclasses import replace
        monkeypatch.setattr(
            "bot.handlers.settings",
            type("S", (), {"scan_mode": "auto", "confirm_threshold": 0.6})(),
        )
        assert handlers._should_scan(self._update("", is_group=True)) is True

    def test_group_no_caption_returns_false(self, monkeypatch):
        monkeypatch.setattr(
            "bot.handlers.settings",
            type("S", (), {"scan_mode": "mention", "confirm_threshold": 0.6})(),
        )
        assert handlers._should_scan(self._update("", is_group=True)) is False

    def test_group_with_at_bot_returns_true(self, monkeypatch):
        monkeypatch.setattr(
            "bot.handlers.settings",
            type("S", (), {"scan_mode": "mention", "confirm_threshold": 0.6})(),
        )
        assert handlers._should_scan(self._update("@bot lưu ảnh", is_group=True)) is True

    def test_group_with_luu_trigger_returns_true(self, monkeypatch):
        monkeypatch.setattr(
            "bot.handlers.settings",
            type("S", (), {"scan_mode": "mention", "confirm_threshold": 0.6})(),
        )
        assert handlers._should_scan(self._update("lưu giúp mình", is_group=True)) is True

    def test_group_with_scan_trigger_returns_true(self, monkeypatch):
        monkeypatch.setattr(
            "bot.handlers.settings",
            type("S", (), {"scan_mode": "mention", "confirm_threshold": 0.6})(),
        )
        assert handlers._should_scan(self._update("scan ảnh này", is_group=True)) is True

    def test_group_with_chot_trigger_returns_true(self, monkeypatch):
        monkeypatch.setattr(
            "bot.handlers.settings",
            type("S", (), {"scan_mode": "mention", "confirm_threshold": 0.6})(),
        )
        assert handlers._should_scan(self._update("chốt đơn", is_group=True)) is True

    def test_group_irrelevant_caption_returns_false(self, monkeypatch):
        monkeypatch.setattr(
            "bot.handlers.settings",
            type("S", (), {"scan_mode": "mention", "confirm_threshold": 0.6})(),
        )
        assert handlers._should_scan(self._update("xin chào", is_group=True)) is False

    def test_private_chat_always_returns_true(self, monkeypatch):
        monkeypatch.setattr(
            "bot.handlers.settings",
            type("S", (), {"scan_mode": "mention", "confirm_threshold": 0.6})(),
        )
        # 1-1 chat, no caption → still True
        assert handlers._should_scan(self._update("", is_group=False)) is True

    def test_private_chat_with_caption_returns_true(self, monkeypatch):
        monkeypatch.setattr(
            "bot.handlers.settings",
            type("S", (), {"scan_mode": "mention", "confirm_threshold": 0.6})(),
        )
        assert handlers._should_scan(self._update("random text", is_group=False)) is True


# ── _check_rate_limit ─────────────────────────────────────────────────────────

class TestCheckRateLimit:
    def test_allows_first_request(self):
        assert handlers._check_rate_limit("user1") is True

    def test_allows_under_limit(self, monkeypatch):
        monkeypatch.setattr("bot.handlers._SCAN_RATE_PER_MIN", 5)
        # 4 requests should all pass
        for _ in range(4):
            result = handlers._check_rate_limit("user_test")
        assert result is True

    def test_blocks_when_over_limit(self, monkeypatch):
        monkeypatch.setattr("bot.handlers._SCAN_RATE_PER_MIN", 3)
        for _ in range(3):
            handlers._check_rate_limit("user_spam")
        assert handlers._check_rate_limit("user_spam") is False

    def test_none_user_always_allowed(self):
        assert handlers._check_rate_limit(None) is True

    def test_isolated_per_user(self, monkeypatch):
        """User A vượt giới hạn không ảnh hưởng User B."""
        monkeypatch.setattr("bot.handlers._SCAN_RATE_PER_MIN", 2)
        handlers._check_rate_limit("user_a")
        handlers._check_rate_limit("user_a")
        # user_a now at limit
        assert handlers._check_rate_limit("user_a") is False
        # user_b still OK
        assert handlers._check_rate_limit("user_b") is True

    def test_old_timestamps_expire(self, monkeypatch):
        """Timestamp cũ hơn 60 giây bị loại bỏ."""
        monkeypatch.setattr("bot.handlers._SCAN_RATE_PER_MIN", 2)
        uid = "user_expiry"
        # Seed with old timestamp (70 seconds ago)
        bucket = handlers._rate_buckets[uid]
        bucket.append(time.monotonic() - 70)
        bucket.append(time.monotonic() - 65)
        # Both old → bucket effectively empty after cleanup
        assert handlers._check_rate_limit(uid) is True


# ── on_photo ──────────────────────────────────────────────────────────────────

def _make_settings(scan_mode="mention", confirm_threshold=0.6):
    return type("S", (), {"scan_mode": scan_mode, "confirm_threshold": confirm_threshold})()


def _make_doc(doc_id: int = 1, doc_type: str = "sale") -> Document:
    doc = MagicMock(spec=Document)
    doc.id = doc_id
    doc.doc_type = doc_type
    doc.party_name = "KH Test"
    doc.total_amount = 500_000
    doc.tracking_code = None
    doc.items = []
    return doc


class TestOnPhoto:
    """Tests for on_photo async handler."""

    async def test_sticker_is_ignored(self, monkeypatch):
        """Sticker phải bị bỏ qua (không gọi _should_scan)."""
        update = FakeUpdate(text="@bot", is_group=True)
        update.message.sticker = {"id": "abc"}  # mark as sticker

        monkeypatch.setattr("bot.handlers.settings", _make_settings())
        await handlers.on_photo(update, FakeContext())
        # No reply expected
        assert update.replies == []

    async def test_should_scan_false_exits_early(self, monkeypatch):
        """Nhóm không có trigger → bỏ qua, không tải ảnh."""
        update = FakeUpdate(text="", is_group=True, photo_url="https://img.zadn.vn/x.jpg")
        monkeypatch.setattr("bot.handlers.settings", _make_settings())
        await handlers.on_photo(update, FakeContext())
        assert update.replies == []

    async def test_doc_type_unknown_sends_skip_reply(self, monkeypatch):
        """doc_type=unknown → reply bỏ qua, không save."""
        update = FakeUpdate(text="@bot", is_group=True, photo_url="https://img.zadn.vn/x.jpg")
        monkeypatch.setattr("bot.handlers.settings", _make_settings())

        fake_data = {
            "doc_type": "unknown", "confidence": 0.10,
            "total_amount": 0, "items": [],
        }

        with (
            patch("bot.zalo_compat.photo_bytes", new_callable=AsyncMock,
                  return_value=(b"fake_img", "image/jpeg")),
            patch("bot.handlers.extract_document", new_callable=AsyncMock,
                  return_value=fake_data),
            patch("bot.handlers.repo.save_extraction_v2") as mock_save,
        ):
            await handlers.on_photo(update, FakeContext())

        mock_save.assert_not_called()
        assert any("không giống" in r.lower() for r in update.replies)

    async def test_duplicate_image_sends_dup_reply(self, monkeypatch):
        """is_duplicate=True → reply thông báo đã lưu trước đó."""
        update = FakeUpdate(text="@bot", is_group=True, photo_url="https://img.zadn.vn/x.jpg")
        monkeypatch.setattr("bot.handlers.settings", _make_settings())

        doc = _make_doc(doc_id=42)
        dup_result = SaveResult(document=doc, is_duplicate=True)
        fake_data = {
            "doc_type": "sale", "confidence": 0.90,
            "total_amount": 500_000, "items": [],
        }

        with (
            patch("bot.zalo_compat.photo_bytes", new_callable=AsyncMock,
                  return_value=(b"fake_img", "image/jpeg")),
            patch("bot.handlers.extract_document", new_callable=AsyncMock,
                  return_value=fake_data),
            patch("bot.handlers.repo.save_extraction_v2", return_value=dup_result),
        ):
            await handlers.on_photo(update, FakeContext())

        # Reply should mention "đã được lưu" or "trước đó" or "#42"
        replies_text = " ".join(update.replies).lower()
        assert "trước đó" in replies_text or "lưu" in replies_text or "42" in replies_text

    async def test_successful_save_calls_save_v2_with_image_hash(self, monkeypatch):
        """Luồng thành công phải gọi save_extraction_v2 với image_hash đúng."""
        import hashlib
        img_bytes = b"real_image_bytes"
        expected_hash = hashlib.sha256(img_bytes).hexdigest()

        update = FakeUpdate(text="@bot", is_group=True, photo_url="https://img.zadn.vn/x.jpg", cid="g1")
        monkeypatch.setattr("bot.handlers.settings", _make_settings())

        doc = _make_doc(doc_id=10)
        success_result = SaveResult(document=doc, is_duplicate=False)
        fake_data = {
            "doc_type": "sale", "confidence": 0.90,
            "total_amount": 500_000, "items": [],
        }

        with (
            patch("bot.zalo_compat.photo_bytes", new_callable=AsyncMock,
                  return_value=(img_bytes, "image/jpeg")),
            patch("bot.handlers.extract_document", new_callable=AsyncMock,
                  return_value=fake_data),
            patch("bot.handlers.repo.save_extraction_v2", return_value=success_result) as mock_save,
        ):
            await handlers.on_photo(update, FakeContext())

        mock_save.assert_called_once()
        call_kwargs = mock_save.call_args.kwargs
        assert call_kwargs["image_hash"] == expected_hash

    async def test_successful_save_sends_saved_reply(self, monkeypatch):
        """Lưu thành công → reply với saved_block."""
        update = FakeUpdate(text="@bot", is_group=True, photo_url="https://img.zadn.vn/x.jpg")
        monkeypatch.setattr("bot.handlers.settings", _make_settings())

        doc = _make_doc(doc_id=7, doc_type="sale")
        success_result = SaveResult(document=doc, is_duplicate=False)
        fake_data = {
            "doc_type": "sale", "confidence": 0.90,
            "total_amount": 500_000, "items": [],
        }

        with (
            patch("bot.zalo_compat.photo_bytes", new_callable=AsyncMock,
                  return_value=(b"img", "image/jpeg")),
            patch("bot.handlers.extract_document", new_callable=AsyncMock,
                  return_value=fake_data),
            patch("bot.handlers.repo.save_extraction_v2", return_value=success_result),
        ):
            await handlers.on_photo(update, FakeContext())

        replies_text = " ".join(update.replies)
        assert "Đã lưu" in replies_text or "lưu" in replies_text.lower()

    async def test_photo_bytes_error_sends_warning_reply(self, monkeypatch):
        """Lỗi tải ảnh → reply cảnh báo, không crash."""
        update = FakeUpdate(text="@bot", is_group=True, photo_url="https://img.zadn.vn/x.jpg")
        monkeypatch.setattr("bot.handlers.settings", _make_settings())

        with patch("bot.zalo_compat.photo_bytes", new_callable=AsyncMock,
                   side_effect=RuntimeError("network error")):
            await handlers.on_photo(update, FakeContext())

        assert any("⚠️" in r or "Không tải" in r for r in update.replies)

    async def test_extract_document_error_sends_warning_reply(self, monkeypatch):
        """extract_document lỗi → reply cảnh báo."""
        update = FakeUpdate(text="@bot", is_group=True, photo_url="https://img.zadn.vn/x.jpg")
        monkeypatch.setattr("bot.handlers.settings", _make_settings())

        with (
            patch("bot.zalo_compat.photo_bytes", new_callable=AsyncMock,
                  return_value=(b"img", "image/jpeg")),
            patch("bot.handlers.extract_document", new_callable=AsyncMock,
                  side_effect=Exception("vision API error")),
        ):
            await handlers.on_photo(update, FakeContext())

        assert any("⚠️" in r for r in update.replies)

    async def test_rate_limit_exceeded_sends_warning(self, monkeypatch):
        """Vượt rate limit → reply thông báo chờ."""
        monkeypatch.setattr("bot.handlers._SCAN_RATE_PER_MIN", 1)
        update = FakeUpdate(text="@bot", is_group=True, photo_url="https://img.zadn.vn/x.jpg", uid="user_rl")
        monkeypatch.setattr("bot.handlers.settings", _make_settings())

        # First call goes through (rate limit 1/min)
        # We need to burn the quota first
        handlers._check_rate_limit("user_rl")

        with patch("bot.zalo_compat.photo_bytes", new_callable=AsyncMock,
                   return_value=(b"img", "image/jpeg")):
            await handlers.on_photo(update, FakeContext())

        # Should be rate limited
        assert any("nhanh" in r.lower() or "giới hạn" in r.lower() or "⏳" in r
                   for r in update.replies)

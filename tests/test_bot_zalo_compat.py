"""Tests for bot/zalo_compat.py — _mime, is_bot_mentioned, is_sticker,
_is_private_ip, _host_allowed.

NOTE: photo_bytes() contains a NameError bug (line 188: `current_url` is
undefined, should be `url`). Tests for photo_bytes are skipped; bug is
documented in the completion report.
"""
from __future__ import annotations

import pytest

from bot.zalo_compat import (
    _host_allowed,
    _is_private_ip,
    _mime,
    is_bot_mentioned,
    is_group,
    is_sticker,
    message_text,
    sender,
    chat_id,
    photo_url,
)
from tests.conftest import FakeChat, FakeMessage, FakeUpdate, FakeUser


# ── _mime ─────────────────────────────────────────────────────────────────────

class TestMime:
    def test_jpeg_url_returns_image_jpeg(self):
        assert _mime("https://cdn.zadn.vn/photo.jpg", None) == "image/jpeg"

    def test_png_url_returns_image_png(self):
        assert _mime("https://cdn.zadn.vn/photo.png", None) == "image/png"

    def test_pdf_url_returns_application_pdf(self):
        assert _mime("https://cdn.zadn.vn/doc.pdf", None) == "application/pdf"

    def test_webp_url_returns_image_webp(self):
        assert _mime("https://cdn.zadn.vn/photo.webp", None) == "image/webp"

    def test_unknown_extension_defaults_to_jpeg(self):
        assert _mime("https://cdn.zadn.vn/photo", None) == "image/jpeg"

    def test_content_type_header_takes_priority_over_extension(self):
        # URL says .png but header says image/webp
        result = _mime("https://cdn.zadn.vn/photo.png", "image/webp")
        assert result == "image/webp"

    def test_pdf_content_type_takes_priority(self):
        result = _mime("https://cdn.zadn.vn/photo.jpg", "application/pdf")
        assert result == "application/pdf"

    def test_content_type_with_charset_stripped(self):
        result = _mime("https://cdn.zadn.vn/img.jpg", "image/png; charset=utf-8")
        assert result == "image/png"

    def test_non_image_content_type_falls_back_to_url(self):
        # Non-image, non-PDF content-type → fall back to URL extension
        result = _mime("https://cdn.zadn.vn/photo.png", "text/html")
        assert result == "image/png"

    def test_uppercase_extension_handled(self):
        result = _mime("https://cdn.zadn.vn/photo.PNG", None)
        # URL lowercased → should detect .png
        assert result == "image/png"


# ── is_bot_mentioned ──────────────────────────────────────────────────────────

class TestIsBotMentioned:
    def _update(self, text: str):
        return FakeUpdate(text=text)

    def test_at_bot_returns_true(self):
        assert is_bot_mentioned(self._update("@bot doanh thu tháng này"))

    def test_at_bot_uppercase_in_text(self):
        # text is lowercased inside is_bot_mentioned
        assert is_bot_mentioned(self._update("@BOT please help"))

    def test_robot_returns_false(self):
        """'robot' không chứa '@bot' → False."""
        assert not is_bot_mentioned(self._update("robot đâu rồi"))

    def test_about_returns_false(self):
        """'about' không phải '@bot' → False."""
        assert not is_bot_mentioned(self._update("about this topic"))

    def test_email_at_example_returns_false(self):
        """Standard email không chứa '@bot' → False."""
        assert not is_bot_mentioned(self._update("gửi tới admin@example.com"))

    def test_no_mention_returns_false(self):
        assert not is_bot_mentioned(self._update("xin chào mọi người"))

    def test_empty_text_returns_false(self):
        assert not is_bot_mentioned(self._update(""))

    def test_bot_names_custom_match(self):
        update = self._update("zalosales thống kê")
        assert is_bot_mentioned(update, bot_names={"zalosales"})

    def test_bot_names_no_match(self):
        update = self._update("không có gì đặc biệt")
        assert not is_bot_mentioned(update, bot_names={"zalosales"})


# ── is_sticker ────────────────────────────────────────────────────────────────

class TestIsSticker:
    def test_normal_message_not_sticker(self):
        update = FakeUpdate(text="hello")
        assert not is_sticker(update)

    def test_sticker_attr_set(self):
        update = FakeUpdate(text="")
        update.message.sticker = {"id": "123"}  # non-None sticker attr
        assert is_sticker(update)

    def test_sticker_id_set(self):
        update = FakeUpdate(text="")
        update.message.sticker_id = "sticker_abc"
        assert is_sticker(update)

    def test_message_type_sticker(self):
        update = FakeUpdate(text="")
        update.message.type = "sticker"
        assert is_sticker(update)

    def test_none_message_not_sticker(self):
        class EmptyUpdate:
            message = None
        assert not is_sticker(EmptyUpdate())


# ── _is_private_ip ────────────────────────────────────────────────────────────

class TestIsPrivateIp:
    def test_loopback_ipv4(self):
        assert _is_private_ip("127.0.0.1")

    def test_loopback_ipv6(self):
        assert _is_private_ip("::1")

    def test_rfc1918_10_block(self):
        assert _is_private_ip("10.0.0.1")

    def test_rfc1918_192_168_block(self):
        assert _is_private_ip("192.168.1.100")

    def test_rfc1918_172_16_block(self):
        assert _is_private_ip("172.16.0.1")

    def test_link_local(self):
        assert _is_private_ip("169.254.1.1")

    def test_public_dns_google(self):
        assert not _is_private_ip("8.8.8.8")

    def test_public_cloudflare(self):
        assert not _is_private_ip("1.1.1.1")


# ── _host_allowed ─────────────────────────────────────────────────────────────

class TestHostAllowed:
    """Uses default allowlist: zadn.vn, zaloapp.com, zalo.me, zdn.vn"""

    def test_exact_match_zadn_vn(self):
        assert _host_allowed("zadn.vn")

    def test_subdomain_of_zadn_vn(self):
        assert _host_allowed("img.zadn.vn")

    def test_deep_subdomain_of_zaloapp_com(self):
        assert _host_allowed("cdn.assets.zaloapp.com")

    def test_zalo_me_allowed(self):
        assert _host_allowed("zalo.me")

    def test_zdn_vn_allowed(self):
        assert _host_allowed("zdn.vn")

    def test_unknown_host_blocked(self):
        assert not _host_allowed("evil.com")

    def test_similar_but_not_subdomain_blocked(self):
        # "notazdnvn.zadn.vn.evil.com" contains "zadn.vn" as substring but not subdomain
        assert not _host_allowed("zadn.vn.evil.com")

    def test_case_insensitive(self):
        assert _host_allowed("IMG.ZADN.VN")


# ── Misc zalo_compat helpers ──────────────────────────────────────────────────

class TestZaloCompatHelpers:
    def test_chat_id_returns_string(self):
        update = FakeUpdate(cid="group123")
        assert chat_id(update) == "group123"

    def test_is_group_true_for_group(self):
        update = FakeUpdate(is_group=True)
        assert is_group(update)

    def test_is_group_false_for_private(self):
        update = FakeUpdate(is_group=False)
        assert not is_group(update)

    def test_sender_returns_id_and_name(self):
        update = FakeUpdate(uid="u42", name="Linh")
        uid, name = sender(update)
        assert uid == "u42"
        assert name == "Linh"

    def test_message_text_strips_whitespace(self):
        update = FakeUpdate(text="  @bot hello  ")
        assert message_text(update) == "@bot hello"

    def test_photo_url_returns_url(self):
        update = FakeUpdate(photo_url="https://img.zadn.vn/pic.jpg")
        assert photo_url(update) == "https://img.zadn.vn/pic.jpg"

    def test_photo_url_none_when_no_photo(self):
        update = FakeUpdate()
        assert photo_url(update) is None

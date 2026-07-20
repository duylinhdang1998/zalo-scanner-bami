"""Tests for bot/handlers.py command handlers — cmd_xoa, on_text, _arg, _period_word."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import bot.handlers as handlers
from tests.conftest import FakeContext, FakeUpdate


def _make_settings(scan_mode="mention", confirm_threshold=0.6):
    return type("S", (), {"scan_mode": scan_mode, "confirm_threshold": confirm_threshold})()


# ── _arg helper ───────────────────────────────────────────────────────────────

class TestArgHelper:
    def test_extracts_argument_after_command(self):
        update = FakeUpdate(text="/doanhthu tháng này")
        assert handlers._arg(update) == "tháng này"

    def test_command_only_returns_empty(self):
        update = FakeUpdate(text="/thongke")
        assert handlers._arg(update) == ""

    def test_non_command_returns_full_text(self):
        update = FakeUpdate(text="câu hỏi tự do")
        assert handlers._arg(update) == "câu hỏi tự do"


# ── _period_word helper ───────────────────────────────────────────────────────

class TestPeriodWord:
    def test_empty_arg_defaults_to_today(self):
        assert handlers._period_word("") == "today"

    def test_normalizes_period_phrase_to_token(self):
        # Chuẩn hoá cụm tiếng Việt → token canonical (resolve() hiểu như nhau).
        assert handlers._period_word("tháng này") == "this_month"
        assert handlers._period_word("hôm qua") == "yesterday"

    def test_extracts_dash_date_from_long_phrase(self):
        # Bug fix: '/baocao báo cáo ngày 19-7-2026' phải trích ra đúng ngày.
        assert handlers._period_word("báo cáo ngày 19-7-2026") == "day:2026-07-19"
        # Thiếu năm → suy ra năm hiện tại (không hardcode để test khỏi phụ thuộc).
        from datetime import date
        y = date.today().year
        assert handlers._period_word("báo cáo ngày 19-7") == f"day:{y}-07-19"
        assert handlers._period_word("19/7") == f"day:{y}-07-19"


# ── cmd_xoa ───────────────────────────────────────────────────────────────────

class TestCmdXoa:
    async def test_valid_id_deletes_and_confirms(self, fresh_db, monkeypatch):
        """Xóa bản ghi tồn tại → reply xác nhận."""
        from db import repository as repo
        from datetime import date
        doc = repo.save_extraction(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url=None,
            data={
                "doc_type": "sale", "confidence": 0.9,
                "doc_date": date.today().isoformat(),
                "party_name": "KH", "total_amount": 100_000, "currency": "VND", "items": [],
            },
        )
        update = FakeUpdate(text=f"/xoa {doc.id}", is_group=True, cid="g1")
        await handlers.cmd_xoa(update, FakeContext())
        assert any("Đã xoá" in r or "xoá" in r for r in update.replies)

    async def test_invalid_id_format_replies_syntax(self):
        update = FakeUpdate(text="/xoa abc", is_group=True, cid="g1")
        await handlers.cmd_xoa(update, FakeContext())
        assert any("Cú pháp" in r or "/xoa" in r for r in update.replies)

    async def test_nonexistent_id_replies_not_found(self, fresh_db):
        update = FakeUpdate(text="/xoa 99999", is_group=True, cid="g1")
        await handlers.cmd_xoa(update, FakeContext())
        assert any("không thấy" in r.lower() or "99999" in r for r in update.replies)

    async def test_hash_prefixed_id(self, fresh_db):
        """#7 → strips hash and parses as 7."""
        from db import repository as repo
        from datetime import date
        doc = repo.save_extraction(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url=None,
            data={
                "doc_type": "sale", "confidence": 0.9,
                "doc_date": date.today().isoformat(),
                "party_name": "KH", "total_amount": 100_000, "currency": "VND", "items": [],
            },
        )
        update = FakeUpdate(text=f"/xoa #{doc.id}", is_group=True, cid="g1")
        await handlers.cmd_xoa(update, FakeContext())
        assert any("Đã xoá" in r or "xoá" in r for r in update.replies)


# ── on_text ───────────────────────────────────────────────────────────────────

class TestOnText:
    async def test_group_without_bot_mention_ignored(self, fresh_db):
        """Nhóm không tag @bot → không trả lời."""
        update = FakeUpdate(text="xin chào mọi người", is_group=True)
        await handlers.on_text(update, FakeContext())
        assert update.replies == []

    async def test_group_with_bot_mention_answered(self, fresh_db):
        """Tag @bot → router.answer() được gọi và trả lời."""
        update = FakeUpdate(text="@bot doanh thu hôm nay", is_group=True, cid="g1")
        with patch("bot.handlers.router.answer", new_callable=AsyncMock,
                   return_value="📊 Doanh thu hôm nay\n• Tổng: 0đ"):
            await handlers.on_text(update, FakeContext())
        assert len(update.replies) > 0
        assert "Doanh thu" in update.replies[-1]

    async def test_private_chat_always_answered(self, fresh_db):
        """1-1 chat không cần @bot."""
        update = FakeUpdate(text="doanh thu hôm nay", is_group=False, cid="g2")
        with patch("bot.handlers.router.answer", new_callable=AsyncMock,
                   return_value="📊 Doanh thu hôm nay\n• Tổng: 0đ"):
            await handlers.on_text(update, FakeContext())
        assert len(update.replies) > 0

    async def test_empty_text_ignored(self):
        """Empty text → không làm gì."""
        update = FakeUpdate(text="", is_group=False)
        await handlers.on_text(update, FakeContext())
        assert update.replies == []

    async def test_router_error_replies_warning(self, fresh_db):
        """router.answer() lỗi → reply cảnh báo."""
        update = FakeUpdate(text="@bot hỏi gì đó", is_group=True, cid="g1")
        with patch("bot.handlers.router.answer", new_callable=AsyncMock,
                   side_effect=Exception("LLM down")):
            await handlers.on_text(update, FakeContext())
        assert any("⚠️" in r for r in update.replies)


# ── cmd_baocao ────────────────────────────────────────────────────────────────

class TestCmdBaocao:
    """cmd_baocao gọi repo.full_report → format_report_block hoặc no_data."""

    async def test_empty_db_replies_no_data(self, fresh_db):
        """/baocao khi DB rỗng → thông báo chưa có dữ liệu."""
        update = FakeUpdate(text="/baocao", is_group=True, cid="g1")
        await handlers.cmd_baocao(update, FakeContext())
        assert len(update.replies) == 1
        reply = update.replies[0]
        assert "chưa có" in reply.lower() or "không suy đoán" in reply.lower()

    async def test_with_data_replies_report_block(self, fresh_db):
        """Khi có dữ liệu → reply chứa 'Báo cáo' và doanh thu."""
        from db import repository as repo_mod
        from datetime import date
        repo_mod.save_extraction(
            group_id="g1", sender_id="u1", sender_name="Linh", image_url=None,
            data={
                "doc_type": "sale", "confidence": 0.9,
                "doc_date": date.today().isoformat(),
                "party_name": "KH A", "total_amount": 500_000,
                "currency": "VND", "status": None, "tracking_code": None, "note": None,
                "items": [],
            },
        )
        update = FakeUpdate(text="/baocao", is_group=True, cid="g1")
        await handlers.cmd_baocao(update, FakeContext())
        assert len(update.replies) == 1
        reply = update.replies[0]
        assert "Báo cáo" in reply or "báo cáo" in reply
        assert "500.000đ" in reply

    async def test_with_data_no_fabricated_numbers(self, fresh_db):
        """Số trong reply phải đến từ DB — không có số bịa."""
        from db import repository as repo_mod
        from datetime import date
        import re
        repo_mod.save_extraction(
            group_id="g1", sender_id="u1", sender_name="Linh", image_url=None,
            data={
                "doc_type": "sale", "confidence": 0.9,
                "doc_date": date.today().isoformat(),
                "party_name": "KH A", "total_amount": 123_456,
                "currency": "VND", "status": None, "tracking_code": None, "note": None,
                "items": [],
            },
        )
        update = FakeUpdate(text="/baocao", is_group=True, cid="g1")
        await handlers.cmd_baocao(update, FakeContext())
        reply = update.replies[0]
        # Reply phải chứa đúng số từ DB
        assert "123.456đ" in reply

    async def test_exception_replies_warning(self, fresh_db):
        """/baocao lỗi nội bộ → reply ⚠️."""
        update = FakeUpdate(text="/baocao", is_group=True, cid="g1")
        with patch("bot.handlers.repo.full_report", side_effect=Exception("DB down")):
            await handlers.cmd_baocao(update, FakeContext())
        assert any("⚠️" in r for r in update.replies)

    async def test_with_period_arg(self, fresh_db):
        """/baocao tháng này → period được parse đúng, không crash."""
        update = FakeUpdate(text="/baocao tháng này", is_group=True, cid="g1")
        await handlers.cmd_baocao(update, FakeContext())
        assert len(update.replies) == 1  # không exception


# ── cmd_khach ─────────────────────────────────────────────────────────────────

class TestCmdKhach:
    """cmd_khach gọi repo.revenue_by_customer(limit=5) → customers_block hoặc no_data."""

    async def test_empty_db_replies_no_data(self, fresh_db):
        """/khach khi DB rỗng → thông báo chưa có."""
        update = FakeUpdate(text="/khach", is_group=True, cid="g1")
        await handlers.cmd_khach(update, FakeContext())
        assert len(update.replies) == 1
        reply = update.replies[0]
        assert "chưa có" in reply.lower() or "không suy đoán" in reply.lower()

    async def test_with_data_replies_customers_block(self, fresh_db):
        """Khi có dữ liệu → reply chứa 'Top khách hàng' và tên khách."""
        from db import repository as repo_mod
        from datetime import date
        repo_mod.save_extraction(
            group_id="g1", sender_id="u1", sender_name="Linh", image_url=None,
            data={
                "doc_type": "sale", "confidence": 0.9,
                "doc_date": date.today().isoformat(),
                "party_name": "Nguyễn Văn A", "total_amount": 750_000,
                "currency": "VND", "status": None, "tracking_code": None, "note": None,
                "items": [],
            },
        )
        update = FakeUpdate(text="/khach", is_group=True, cid="g1")
        await handlers.cmd_khach(update, FakeContext())
        assert len(update.replies) == 1
        reply = update.replies[0]
        assert "khách hàng" in reply.lower() or "Top khách" in reply
        assert "Nguyễn Văn A" in reply
        assert "750.000đ" in reply

    async def test_limit_is_5(self, fresh_db):
        """Phải gọi revenue_by_customer với limit=5."""
        update = FakeUpdate(text="/khach", is_group=True, cid="g1")
        with patch("bot.handlers.repo.revenue_by_customer", return_value=[]) as mock_fn:
            await handlers.cmd_khach(update, FakeContext())
        assert mock_fn.called
        # Hàm được gọi với positional args: (gid, start, end, 5)
        call_args = mock_fn.call_args
        # limit là arg thứ 4 (index 3)
        pos_args = call_args[0] if call_args[0] else ()
        kw_args = call_args[1] if call_args[1] else {}
        call_limit = kw_args.get("limit") or (pos_args[3] if len(pos_args) > 3 else None)
        assert call_limit == 5

    async def test_only_sale_counted(self, fresh_db):
        """order không được tính vào top khách hàng."""
        from db import repository as repo_mod
        from datetime import date
        # Chỉ lưu order, không sale
        repo_mod.save_extraction(
            group_id="g1", sender_id="u1", sender_name="Linh", image_url=None,
            data={
                "doc_type": "order", "confidence": 0.9,
                "doc_date": date.today().isoformat(),
                "party_name": "Người nhận X", "total_amount": 200_000,
                "currency": "VND", "status": "cho_giao", "tracking_code": None, "note": None,
                "items": [],
            },
        )
        update = FakeUpdate(text="/khach", is_group=True, cid="g1")
        await handlers.cmd_khach(update, FakeContext())
        reply = update.replies[0]
        # Không có sale → no_data
        assert "chưa có" in reply.lower() or "không suy đoán" in reply.lower()

    async def test_exception_replies_warning(self, fresh_db):
        """/khach lỗi nội bộ → reply ⚠️."""
        update = FakeUpdate(text="/khach", is_group=True, cid="g1")
        with patch("bot.handlers.repo.revenue_by_customer", side_effect=Exception("DB down")):
            await handlers.cmd_khach(update, FakeContext())
        assert any("⚠️" in r for r in update.replies)

    async def test_with_period_arg(self, fresh_db):
        """/khach tháng này → period parse đúng."""
        update = FakeUpdate(text="/khach tháng này", is_group=True, cid="g1")
        await handlers.cmd_khach(update, FakeContext())
        assert len(update.replies) == 1

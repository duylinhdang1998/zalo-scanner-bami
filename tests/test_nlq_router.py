"""Tests for nlq/router.py — keyword routing, period detection, answer()."""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nlq.router import _keyword_period, _keyword_route, answer


# ── _keyword_period ───────────────────────────────────────────────────────────

class TestKeywordPeriod:
    def test_hom_nay(self):
        assert _keyword_period("doanh thu hôm nay") == "today"

    def test_hom_nay_no_diacritic(self):
        assert _keyword_period("doanh thu hom nay") == "today"

    def test_hom_qua(self):
        assert _keyword_period("báo cáo hôm qua") == "yesterday"

    def test_hom_qua_no_diacritic(self):
        assert _keyword_period("bao cao hom qua") == "yesterday"

    def test_tuan_nay(self):
        assert _keyword_period("tuần này bán được bao nhiêu") == "this_week"

    def test_tuan_nay_no_diacritic(self):
        assert _keyword_period("tuan nay ban duoc") == "this_week"

    def test_tuan_truoc(self):
        assert _keyword_period("tuần trước") == "last_week"

    def test_tuan_truoc_no_diacritic(self):
        assert _keyword_period("tuan truoc") == "last_week"

    def test_thang_nay(self):
        assert _keyword_period("tháng này") == "this_month"

    def test_thang_nay_no_diacritic(self):
        assert _keyword_period("thang nay") == "this_month"

    def test_thang_truoc(self):
        assert _keyword_period("tháng trước") == "last_month"

    def test_thang_truoc_no_diacritic(self):
        assert _keyword_period("thang truoc") == "last_month"

    def test_tat_ca(self):
        assert _keyword_period("tất cả") == "all"

    def test_toan_bo(self):
        assert _keyword_period("toàn bộ") == "all"

    def test_default_today_on_unknown(self):
        assert _keyword_period("câu hỏi không rõ") == "today"

    def test_default_today_on_empty(self):
        assert _keyword_period("") == "today"


# ── _keyword_route ────────────────────────────────────────────────────────────

class TestKeywordRoute:
    def test_doanhthu_intent(self):
        intent, period, limit = _keyword_route("doanh thu tháng này")
        assert intent == "revenue"
        assert period == "this_month"

    def test_doanhthu_alias_doanh_so(self):
        intent, _, _ = _keyword_route("doanh số hôm nay")
        assert intent == "revenue"

    def test_doanhthu_alias_tong_tien(self):
        intent, _, _ = _keyword_route("tổng tiền tuần này")
        assert intent == "revenue"

    def test_doanhthu_alias_ban_duoc(self):
        intent, _, _ = _keyword_route("bán được bao nhiêu hôm nay")
        assert intent == "revenue"

    def test_top_intent(self):
        intent, period, limit = _keyword_route("top 3 sản phẩm bán chạy hôm nay")
        assert intent == "top_products"
        assert limit == 3

    def test_top_default_limit_5(self):
        intent, _, limit = _keyword_route("top bán chạy")
        assert intent == "top_products"
        assert limit == 5

    def test_ban_chay_alias(self):
        intent, _, _ = _keyword_route("bán chạy nhất tuần này")
        assert intent == "top_products"

    def test_don_hang_intent(self):
        intent, _, _ = _keyword_route("đơn hàng hôm nay")
        assert intent == "orders"

    def test_don_hang_no_diacritic(self):
        intent, _, _ = _keyword_route("don hang hom nay")
        assert intent == "orders"

    def test_trang_thai_intent(self):
        intent, _, _ = _keyword_route("trạng thái đơn hôm nay")
        assert intent == "orders"

    def test_cho_giao_intent(self):
        intent, _, _ = _keyword_route("chờ giao hôm nay")
        assert intent == "orders"

    def test_sellers_intent_theo_nguoi(self):
        intent, _, _ = _keyword_route("doanh thu theo người tháng này")
        assert intent == "sellers"

    def test_sellers_intent_nhan_vien(self):
        intent, _, _ = _keyword_route("nhân viên bán được gì tuần này")
        assert intent == "sellers"

    def test_sellers_intent_ai_ban(self):
        intent, _, _ = _keyword_route("ai bán nhiều nhất hôm nay")
        assert intent == "sellers"

    def test_unrecognized_returns_none_intent(self):
        intent, period, limit = _keyword_route("xin chào bot")
        assert intent is None

    def test_period_extracted_from_unrecognized(self):
        # Mặc dù không nhận ra intent, period vẫn phải được trích xuất
        _, period, _ = _keyword_route("câu hỏi mơ hồ tháng này")
        assert period == "this_month"


# ── answer() ─────────────────────────────────────────────────────────────────

class TestAnswer:
    """Test answer() với mock DB và mock LLM fallback."""

    async def test_answer_revenue_intent(self, fresh_db):
        """answer("doanh thu hôm nay") → revenue_block format."""
        result = await answer("doanh thu hôm nay", "g1")
        assert "Doanh thu" in result
        # Empty DB → 0đ
        assert "0đ" in result

    async def test_answer_top_products_intent(self, fresh_db):
        """answer("top bán chạy") → top_products_block or empty message."""
        result = await answer("top bán chạy hôm nay", "g1")
        # Empty DB → "Chưa có dữ liệu sản phẩm"
        assert "sản phẩm" in result.lower()

    async def test_answer_orders_intent(self, fresh_db):
        """answer("đơn hàng") → orders_block."""
        result = await answer("đơn hàng hôm nay", "g1")
        assert "đơn hàng" in result.lower()

    async def test_answer_sellers_intent(self, fresh_db):
        """answer("theo người") → sellers_block."""
        result = await answer("doanh thu theo người hôm nay", "g1")
        assert "Doanh thu theo người" in result or "Chưa có" in result

    async def test_answer_falls_back_to_llm_when_no_keyword(self, fresh_db):
        """Khi không nhận ra keyword, gọi LLM phân loại."""
        fake_llm_response = {"intent": "revenue", "period": "today", "limit": 5}
        with patch("nlq.router.text_json", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = fake_llm_response
            result = await answer("hỏi thứ gì đó không rõ", "g1")
        mock_llm.assert_awaited_once()
        # Phải có dạng revenue_block
        assert "Doanh thu" in result

    async def test_answer_llm_fallback_uses_out_of_scope_on_error(self, fresh_db):
        """LLM lỗi + câu không có sales signal → out_of_scope (an toàn, không phịa doanh thu).

        Câu bán hàng rõ ("doanh thu hôm nay") đã bị _keyword_route bắt trước khi đến LLM.
        Câu mơ hồ không có keyword bán hàng → khi LLM down → out_of_scope là đúng.
        """
        from vision.beeknoee import BeeknoeeError
        with patch("nlq.router.text_json", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = BeeknoeeError("LLM down")
            result = await answer("câu hỏi mơ hồ", "g1")
        # Không crash; LLM lỗi → out_of_scope, không trả doanh thu giả
        assert "ngoài phạm vi" in result.lower() or "chỉ trả lời" in result.lower()

    async def test_answer_with_seeded_data(self, fresh_db):
        """answer() với DB có dữ liệu → số thực."""
        from db import repository as repo
        today = date.today().isoformat()
        repo.save_extraction(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url=None,
            data={
                "doc_type": "sale", "confidence": 0.9,
                "doc_date": today, "party_name": "KH",
                "total_amount": 500_000, "currency": "VND",
                "items": [],
            },
        )
        result = await answer("doanh thu hôm nay", "g1")
        assert "500.000đ" in result

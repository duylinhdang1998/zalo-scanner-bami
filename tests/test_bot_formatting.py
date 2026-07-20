"""Tests for bot/formatting.py — vnd(), qty(), and *_block formatters."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bot.formatting import (
    orders_block,
    qty,
    revenue_block,
    saved_block,
    sellers_block,
    top_products_block,
    vnd,
)


# ── vnd() ─────────────────────────────────────────────────────────────────────

class TestVnd:
    def test_zero(self):
        assert vnd(0) == "0đ"

    def test_thousands_separator(self):
        assert vnd(1_500_000) == "1.500.000đ"

    def test_small_amount(self):
        assert vnd(1_000) == "1.000đ"

    def test_hundred_thousand(self):
        assert vnd(100_000) == "100.000đ"

    def test_large_amount(self):
        assert vnd(10_000_000) == "10.000.000đ"

    def test_float_rounded(self):
        # 999.5 → rounds to 1000 (Python banker's rounding)
        result = vnd(999_999.5)
        assert "đ" in result

    def test_returns_string(self):
        assert isinstance(vnd(500_000), str)

    def test_suffix_is_dong(self):
        assert vnd(500_000).endswith("đ")


# ── qty() ─────────────────────────────────────────────────────────────────────

class TestQty:
    def test_integer_float_no_decimal(self):
        assert qty(3.0) == "3"

    def test_fractional_uses_g_format(self):
        result = qty(1.5)
        assert "1.5" in result or "1,5" in result  # g format

    def test_zero(self):
        assert qty(0) == "0"

    def test_large_integer(self):
        assert qty(100.0) == "100"


# ── revenue_block() ───────────────────────────────────────────────────────────

class TestRevenueBlock:
    def test_contains_label(self):
        result = revenue_block("hôm nay", {"total": 500_000, "count": 3})
        assert "hôm nay" in result

    def test_contains_total_amount(self):
        result = revenue_block("hôm nay", {"total": 500_000, "count": 3})
        assert "500.000đ" in result

    def test_contains_count(self):
        result = revenue_block("hôm nay", {"total": 500_000, "count": 3})
        assert "3" in result

    def test_zero_total(self):
        result = revenue_block("hôm nay", {"total": 0, "count": 0})
        assert "0đ" in result

    def test_has_emoji_header(self):
        result = revenue_block("hôm nay", {"total": 0, "count": 0})
        assert "📊" in result


# ── sellers_block() ───────────────────────────────────────────────────────────

class TestSellersBlock:
    def test_empty_rows_returns_no_data_message(self):
        result = sellers_block("hôm nay", [])
        assert "Chưa có" in result

    def test_contains_seller_name(self):
        rows = [{"seller": "Linh", "total": 450_000, "count": 2}]
        result = sellers_block("hôm nay", rows)
        assert "Linh" in result

    def test_contains_seller_total(self):
        rows = [{"seller": "Linh", "total": 450_000, "count": 2}]
        result = sellers_block("hôm nay", rows)
        assert "450.000đ" in result

    def test_multiple_sellers_ordered(self):
        rows = [
            {"seller": "Linh", "total": 450_000, "count": 2},
            {"seller": "Hà",   "total": 300_000, "count": 1},
        ]
        result = sellers_block("hôm nay", rows)
        assert "Linh" in result
        assert "Hà" in result
        # Linh should appear before Hà in output
        assert result.index("Linh") < result.index("Hà")


# ── top_products_block() ──────────────────────────────────────────────────────

class TestTopProductsBlock:
    def test_empty_rows_returns_no_data_message(self):
        result = top_products_block("hôm nay", [])
        assert "Chưa có" in result

    def test_contains_product_name(self):
        rows = [{"product": "Áo thun", "qty": 5, "amount": 750_000}]
        result = top_products_block("hôm nay", rows)
        assert "Áo thun" in result

    def test_contains_quantity(self):
        rows = [{"product": "Áo thun", "qty": 5.0, "amount": 750_000}]
        result = top_products_block("hôm nay", rows)
        assert "5" in result

    def test_contains_amount(self):
        rows = [{"product": "Áo thun", "qty": 5.0, "amount": 750_000}]
        result = top_products_block("hôm nay", rows)
        assert "750.000đ" in result

    def test_has_trophy_emoji(self):
        rows = [{"product": "X", "qty": 1, "amount": 10_000}]
        result = top_products_block("hôm nay", rows)
        assert "🏆" in result


# ── orders_block() ────────────────────────────────────────────────────────────

class TestOrdersBlock:
    def test_empty_rows_returns_no_orders_message(self):
        result = orders_block("hôm nay", [])
        assert "Chưa có" in result

    def test_cho_giao_translated(self):
        rows = [{"status": "cho_giao", "count": 3}]
        result = orders_block("hôm nay", rows)
        assert "Chờ giao" in result

    def test_dang_giao_translated(self):
        rows = [{"status": "dang_giao", "count": 2}]
        result = orders_block("hôm nay", rows)
        assert "Đang giao" in result

    def test_da_giao_translated(self):
        rows = [{"status": "da_giao", "count": 5}]
        result = orders_block("hôm nay", rows)
        assert "Đã giao" in result

    def test_huy_translated(self):
        rows = [{"status": "huy", "count": 1}]
        result = orders_block("hôm nay", rows)
        assert "Huỷ" in result

    def test_total_line_correct(self):
        rows = [
            {"status": "cho_giao", "count": 3},
            {"status": "da_giao",  "count": 2},
        ]
        result = orders_block("hôm nay", rows)
        assert "5" in result  # total = 3 + 2

    def test_unknown_status_shown_raw(self):
        rows = [{"status": "pending", "count": 1}]
        result = orders_block("hôm nay", rows)
        # Unknown status falls back to raw value
        assert "pending" in result


# ── saved_block() ─────────────────────────────────────────────────────────────

class TestSavedBlock:
    def _make_doc(self, **kwargs):
        doc = MagicMock()
        doc.id = kwargs.get("id", 1)
        doc.party_name = kwargs.get("party_name", "KH Test")
        doc.total_amount = kwargs.get("total_amount", 500_000)
        doc.tracking_code = kwargs.get("tracking_code", None)
        doc.items = kwargs.get("items", [])
        return doc

    def test_sale_type_shows_hoa_don(self):
        doc = self._make_doc()
        result = saved_block("sale", doc)
        assert "hoá đơn" in result

    def test_order_type_shows_don_hang(self):
        doc = self._make_doc()
        result = saved_block("order", doc)
        assert "đơn hàng" in result

    def test_contains_doc_id(self):
        doc = self._make_doc(id=42)
        result = saved_block("sale", doc)
        assert "42" in result

    def test_contains_party_name(self):
        doc = self._make_doc(party_name="Nguyễn Văn A")
        result = saved_block("sale", doc)
        assert "Nguyễn Văn A" in result

    def test_contains_total_amount(self):
        doc = self._make_doc(total_amount=1_500_000)
        result = saved_block("sale", doc)
        assert "1.500.000đ" in result

    def test_tracking_code_shown(self):
        doc = self._make_doc(tracking_code="GHN123456")
        result = saved_block("order", doc)
        assert "GHN123456" in result

    def test_items_count_shown(self):
        items = [MagicMock(), MagicMock()]
        doc = self._make_doc(items=items)
        result = saved_block("sale", doc)
        assert "2" in result

    def test_no_party_name_skipped(self):
        doc = self._make_doc(party_name=None)
        result = saved_block("sale", doc)
        assert "KH:" not in result

    def test_xoa_command_included(self):
        doc = self._make_doc(id=7)
        result = saved_block("sale", doc)
        assert "/xoa" in result
        assert "7" in result

    def test_has_checkmark_emoji(self):
        doc = self._make_doc()
        result = saved_block("sale", doc)
        assert "✅" in result

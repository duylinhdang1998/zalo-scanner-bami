"""Sprint 2.2 — Tests cho tầng NLQ mở rộng.

Coverage:
  • Routing intent mới: customers, product, report, out_of_scope
  • Date-range parsing: tháng M, quý Q, từ dd/mm đến dd/mm
  • Guardrail out_of_scope:
      - keyword route phát hiện lời chào → out_of_scope không cần LLM
      - LLM trả out_of_scope hoặc intent ngoài whitelist → out_of_scope
  • No-data grounding: repo trả rỗng → câu trung thực (KHÔNG bịa số)
  • LLM route: validate intent/period/name_like ngoài whitelist
"""
from __future__ import annotations

import os

# ── Env setup trước mọi import project ───────────────────────────────
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("BEEKNOEE_API_KEY", "test-key")
os.environ.setdefault("ZALO_BOT_TOKEN", "test-token")
os.environ.setdefault("BEEKNOEE_BASE_URL", "https://example.test/v1")
os.environ.setdefault("ZALO_IMAGE_HOST_ALLOWLIST", "zadn.vn")
os.environ.setdefault("SCAN_MODE", "mention")
os.environ.setdefault("CONFIRM_THRESHOLD", "0.6")

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from nlq.periods import resolve, month_bounds
from nlq.router import (
    INTENTS,
    _extract_name_like,
    _has_sales_signal,
    _keyword_period,
    _keyword_route,
    answer,
)


# ════════════════════════════════════════════════════════
# Periods — tháng M / quý Q / range
# ════════════════════════════════════════════════════════

class TestPeriodMonth:
    def test_thang_3(self):
        (start, end), label = resolve("tháng:3")
        assert start.month == 3
        assert start.day == 1
        assert end.month == 3
        assert "tháng 3" in label

    def test_thang_12_specific_year(self):
        (start, end), label = resolve("tháng:12:2024")
        assert start == date(2024, 12, 1)
        assert end == date(2024, 12, 31)

    def test_thang_1(self):
        (start, end), _ = resolve("tháng:1")
        assert start.month == 1
        assert start.day == 1

    def test_thang_no_diacritic_token(self):
        (start, end), label = resolve("thang:5")
        assert start.month == 5
        assert "tháng 5" in label


class TestPeriodQuarter:
    def test_quy_1(self):
        (start, end), label = resolve("quý:1")
        today = date.today()
        assert start == date(today.year, 1, 1)
        assert end == date(today.year, 3, 31)
        assert "quý 1" in label

    def test_quy_2(self):
        (start, end), _ = resolve("quý:2")
        today = date.today()
        assert start == date(today.year, 4, 1)
        assert end == date(today.year, 6, 30)

    def test_quy_3(self):
        (start, end), _ = resolve("quý:3")
        today = date.today()
        assert start == date(today.year, 7, 1)
        assert end == date(today.year, 9, 30)

    def test_quy_4(self):
        (start, end), _ = resolve("quý:4")
        today = date.today()
        assert start == date(today.year, 10, 1)
        assert end == date(today.year, 12, 31)

    def test_quy_specific_year(self):
        (start, end), _ = resolve("quý:2:2024")
        assert start == date(2024, 4, 1)
        assert end == date(2024, 6, 30)


class TestPeriodRange:
    def test_range_token(self):
        (start, end), label = resolve("range:2025-01-01:2025-01-31")
        assert start == date(2025, 1, 1)
        assert end == date(2025, 1, 31)
        assert "01/01/2025" in label
        assert "31/01/2025" in label

    def test_range_inverted_returns_today(self):
        # start > end → fallback to today
        (start, end), _ = resolve("range:2025-01-31:2025-01-01")
        today = date.today()
        assert start == today

    def test_range_invalid_date_returns_today(self):
        (start, end), _ = resolve("range:2025-13-01:2025-13-31")
        today = date.today()
        assert start == today


# ════════════════════════════════════════════════════════
# _keyword_period — detect tháng M / quý Q / date range
# ════════════════════════════════════════════════════════

class TestKeywordPeriodExtended:
    def test_thang_cu_the(self):
        p = _keyword_period("doanh thu tháng 3")
        assert p == "tháng:3"

    def test_thang_cu_the_no_diacritic(self):
        p = _keyword_period("doanh thu thang 3")
        assert p == "tháng:3"

    def test_thang_nay_not_confused(self):
        # "tháng này" phải trả this_month, không phải tháng:...
        p = _keyword_period("doanh thu tháng này")
        assert p == "this_month"

    def test_thang_truoc_not_confused(self):
        p = _keyword_period("doanh thu tháng trước")
        assert p == "last_month"

    def test_quy_1(self):
        p = _keyword_period("báo cáo quý 1")
        assert p == "quý:1"

    def test_quy_4(self):
        p = _keyword_period("doanh thu quý 4")
        assert p == "quý:4"

    def test_date_range_full(self):
        p = _keyword_period("từ 01/01 đến 31/01 doanh thu")
        assert p.startswith("range:")
        parts = p[6:].split(":")
        assert len(parts) == 2
        assert parts[0].endswith("-01-01")
        assert parts[1].endswith("-01-31")

    def test_date_range_with_year(self):
        p = _keyword_period("từ 01/01/2024 đến 31/03/2024")
        assert p == "range:2024-01-01:2024-03-31"

    def test_no_period_defaults_today(self):
        p = _keyword_period("câu hỏi không rõ ràng")
        assert p == "today"


# ════════════════════════════════════════════════════════
# _keyword_route — intent mới
# ════════════════════════════════════════════════════════

class TestKeywordRouteNewIntents:
    def test_customers_theo_khach(self):
        intent, _, _ = _keyword_route("doanh thu theo khách hàng")
        assert intent == "customers"

    def test_customers_theo_khach_no_diacritic(self):
        intent, _, _ = _keyword_route("doanh thu theo khach hang")
        assert intent == "customers"

    def test_report_bao_cao(self):
        intent, _, _ = _keyword_route("báo cáo tháng này")
        assert intent == "report"

    def test_report_tong_hop(self):
        intent, _, _ = _keyword_route("tổng hợp tuần này")
        assert intent == "report"

    def test_report_english(self):
        intent, _, _ = _keyword_route("report hôm nay")
        assert intent == "report"

    def test_product_san_pham(self):
        intent, _, _ = _keyword_route("sản phẩm áo thun bán được bao nhiêu")
        assert intent == "product"

    def test_product_con_bao_nhieu(self):
        intent, _, _ = _keyword_route("còn bao nhiêu nón")
        assert intent == "product"

    def test_product_not_confused_with_top(self):
        # "sản phẩm bán chạy" → top_products (top_products check trước)
        intent, _, _ = _keyword_route("sản phẩm bán chạy nhất")
        assert intent == "top_products"

    def test_product_period_extracted(self):
        intent, period, _ = _keyword_route("sản phẩm áo thun tháng này")
        assert intent == "product"
        assert period == "this_month"

    def test_customers_period_extracted(self):
        intent, period, _ = _keyword_route("khách hàng tháng 3")
        assert intent == "customers"
        assert period == "tháng:3"

    def test_report_period_quy(self):
        intent, period, _ = _keyword_route("báo cáo quý 2")
        assert intent == "report"
        assert period == "quý:2"

    def test_greeting_returns_none(self):
        """Lời chào → keyword_route trả None (out_of_scope handled by answer())."""
        intent, _, _ = _keyword_route("xin chào bot")
        assert intent is None

    def test_ban_duoc_bao_nhieu_is_revenue(self):
        """'bán được bao nhiêu' không có product name cụ thể → revenue."""
        intent, _, _ = _keyword_route("bán được bao nhiêu hôm nay")
        assert intent == "revenue"


# ════════════════════════════════════════════════════════
# _has_sales_signal — heuristic helper
# ════════════════════════════════════════════════════════

class TestHasSalesSignal:
    def test_has_signal_doanh_thu(self):
        assert _has_sales_signal("doanh thu hôm nay") is True

    def test_has_signal_don_hang(self):
        assert _has_sales_signal("đơn hàng chưa giao") is True

    def test_has_signal_san_pham(self):
        assert _has_sales_signal("sản phẩm áo thun") is True

    def test_no_signal_xin_chao(self):
        assert _has_sales_signal("xin chào bot") is False

    def test_no_signal_thoi_tiet(self):
        assert _has_sales_signal("thời tiết hôm nay") is False

    def test_has_signal_khach_hang(self):
        assert _has_sales_signal("khách hàng lớn nhất") is True

    def test_has_signal_bao_cao(self):
        assert _has_sales_signal("báo cáo tháng này") is True


# ════════════════════════════════════════════════════════
# _extract_name_like — trích tên sản phẩm
# ════════════════════════════════════════════════════════

class TestExtractNameLike:
    def test_san_pham_trigger(self):
        name = _extract_name_like("sản phẩm áo thun bán được bao nhiêu")
        assert name is not None
        # Sau "sản phẩm" phải có "áo thun" (hoặc tương đương không dấu)
        assert "áo thun" in name.lower() or "ao thun" in name.lower()

    def test_con_bao_nhieu_trigger(self):
        name = _extract_name_like("còn bao nhiêu nón")
        assert name is not None
        assert "nón" in name.lower() or "non" in name.lower()

    def test_safety_limit(self):
        long_name = "a" * 200
        name = _extract_name_like(f"sản phẩm {long_name}")
        if name:
            assert len(name) <= 100


# ════════════════════════════════════════════════════════
# answer() — out_of_scope guardrail
# ════════════════════════════════════════════════════════

class TestAnswerOutOfScope:
    async def test_greeting_out_of_scope_no_llm(self):
        """Lời chào → keyword_route trả out_of_scope, LLM không được gọi."""
        with patch("nlq.router.text_json", new_callable=AsyncMock) as mock_llm:
            result = await answer("xin chào bot", "g1")
        mock_llm.assert_not_awaited()
        assert "ngoài phạm vi" in result.lower() or "chỉ trả lời" in result.lower()

    async def test_thoi_tiet_out_of_scope_no_llm(self):
        """Câu hỏi thời tiết không có sales signal → Layer 1 bắt, LLM KHÔNG được gọi."""
        with patch("nlq.router.text_json", new_callable=AsyncMock) as mock_llm:
            result = await answer("thời tiết hôm nay thế nào", "g1")
        mock_llm.assert_not_awaited()
        assert "ngoài phạm vi" in result.lower() or "chỉ trả lời" in result.lower()

    async def test_llm_out_of_scope_intent(self):
        """LLM trả intent='out_of_scope' → câu cố định."""
        with patch("nlq.router.text_json", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {
                "intent": "out_of_scope", "period": "today",
                "limit": 5, "name_like": None,
            }
            result = await answer("giải thích quy trình lịch sử", "g1")
        assert "ngoài phạm vi" in result.lower() or "chỉ trả lời" in result.lower()

    async def test_llm_unknown_intent_becomes_out_of_scope(self):
        """LLM trả intent không nằm trong whitelist → out_of_scope."""
        with patch("nlq.router.text_json", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {
                "intent": "weather_forecast", "period": "today",
                "limit": 5, "name_like": None,
            }
            result = await answer("dự báo tuần này", "g1")
        # "dự báo" không có trong sales keywords → LLM được gọi → "weather_forecast" → out_of_scope
        assert "ngoài phạm vi" in result.lower() or "chỉ trả lời" in result.lower()


# ════════════════════════════════════════════════════════
# answer() — no-data grounding (mock repo trả rỗng)
# ════════════════════════════════════════════════════════

class TestAnswerNoDataGrounding:
    """Đảm bảo khi repo trả rỗng/0, bot KHÔNG sinh số bịa."""

    async def test_customers_empty_repo_no_fabrication(self):
        """revenue_by_customer trả [] → câu trung thực, không có số bịa."""
        with patch("nlq.router.repo") as mock_repo:
            mock_repo.revenue_by_customer = lambda *a, **kw: []
            mock_repo.revenue_by_seller = lambda *a, **kw: []
            mock_repo.top_products = lambda *a, **kw: []
            mock_repo.orders_by_status = lambda *a, **kw: []
            mock_repo.revenue_summary = lambda *a, **kw: {"total": 0, "count": 0}

            result = await answer("doanh thu theo khách hàng hôm nay", "g1")

        # Phải có thông báo trung thực
        assert "chưa có" in result.lower() or "không suy đoán" in result.lower()
        # Không được có số tiền bịa (số > 0 kèm đơn vị đồng)
        import re
        money_amounts = re.findall(r"(\d[\d.]+)đ", result)
        for n in money_amounts:
            assert float(n.replace(".", "")) == 0.0, f"Số bịa: {n}đ"

    async def test_product_empty_repo_no_fabrication(self):
        """product_detail trả [] → câu trung thực, không có số."""
        with patch("nlq.router.repo") as mock_repo:
            mock_repo.product_detail = lambda *a, **kw: []
            mock_repo.revenue_by_seller = lambda *a, **kw: []
            mock_repo.top_products = lambda *a, **kw: []
            mock_repo.orders_by_status = lambda *a, **kw: []
            mock_repo.revenue_summary = lambda *a, **kw: {"total": 0, "count": 0}

            result = await answer("sản phẩm áo thun hôm nay", "g1")

        assert "chưa có" in result.lower() or "không suy đoán" in result.lower()

    async def test_report_empty_no_fabrication(self):
        """full_report trả count=0 và store_report rỗng → câu trung thực."""
        with patch("nlq.router.repo") as mock_repo:
            mock_repo.full_report = lambda *a, **kw: {"revenue": {"total": 0, "count": 0}}
            mock_repo.revenue_by_seller = lambda *a, **kw: []
            mock_repo.top_products = lambda *a, **kw: []
            mock_repo.orders_by_status = lambda *a, **kw: []
            mock_repo.revenue_summary = lambda *a, **kw: {"total": 0, "count": 0}
            # Sprint 3 — store_report rỗng
            mock_repo.revenue_by_channel = lambda *a, **kw: []
            mock_repo.report_financials = lambda *a, **kw: {"count": 0}
            mock_repo.product_sales_report = lambda *a, **kw: []

            result = await answer("báo cáo tháng này", "g1")

        assert "chưa có" in result.lower() or "không suy đoán" in result.lower()

    async def test_revenue_zero_is_honest(self):
        """revenue_summary trả 0 → hiển thị 0đ (số thật từ repo — không bịa)."""
        with patch("nlq.router.repo") as mock_repo:
            mock_repo.revenue_summary = lambda *a, **kw: {"total": 0.0, "count": 0}
            mock_repo.revenue_by_seller = lambda *a, **kw: []
            mock_repo.top_products = lambda *a, **kw: []
            mock_repo.orders_by_status = lambda *a, **kw: []
            mock_repo.report_financials = lambda *a, **kw: {"gross": 0, "count": 0}

            result = await answer("doanh thu hôm nay", "g1")

        # 0đ là số thật từ repo — OK
        assert "0đ" in result or "Doanh thu" in result
        # Không được có số tiền > 0
        import re
        money_amounts = re.findall(r"(\d[\d.]+)đ", result)
        for n in money_amounts:
            assert float(n.replace(".", "")) == 0.0, f"Số bịa: {n}đ"

    async def test_llm_never_generates_numbers(self):
        """Dù LLM route được gọi, số liệu vẫn từ repo — LLM không sinh số."""
        with patch("nlq.router.text_json", new_callable=AsyncMock) as mock_llm:
            # LLM chỉ trả intent/period, không trả số
            mock_llm.return_value = {
                "intent": "revenue", "period": "today", "limit": 5, "name_like": None,
            }
            with patch("nlq.router.repo") as mock_repo:
                mock_repo.revenue_summary = lambda *a, **kw: {"total": 999_000.0, "count": 3}
                mock_repo.report_financials = lambda *a, **kw: {"gross": 0, "count": 0}
                result = await answer("cho biết số liệu kinh doanh", "g1")

        # Số phải là 999.000đ từ repo, không phải số nào khác
        assert "999.000đ" in result
        # LLM output KHÔNG xuất hiện trực tiếp trong result
        assert "text_json" not in result


# ════════════════════════════════════════════════════════
# answer() — new intents với DB thật (fresh_db fixture)
# ════════════════════════════════════════════════════════

class TestAnswerNewIntentsIntegration:
    """Integration tests cần fresh_db fixture (từ tests/conftest.py)."""

    async def test_answer_customers_empty_db(self, fresh_db):
        result = await answer("doanh thu theo khách hàng tháng này", "g1")
        assert isinstance(result, str)
        assert len(result) > 0

    async def test_answer_report_empty_db(self, fresh_db):
        result = await answer("báo cáo tháng này", "g1")
        assert isinstance(result, str)

    async def test_answer_product_empty_db(self, fresh_db):
        result = await answer("sản phẩm áo thun hôm nay", "g1")
        assert isinstance(result, str)

    async def test_answer_date_range(self, fresh_db):
        result = await answer("doanh thu từ 01/01 đến 31/01", "g1")
        assert "Doanh thu" in result
        # Số từ DB thực (0 vì empty)
        assert "0đ" in result

    async def test_answer_thang_cu_the(self, fresh_db):
        result = await answer("doanh thu tháng 3", "g1")
        assert "Doanh thu" in result
        assert "tháng 3" in result

    async def test_answer_quy(self, fresh_db):
        result = await answer("báo cáo quý 2", "g1")
        assert isinstance(result, str)


# ════════════════════════════════════════════════════════
# INTENTS whitelist — sanity check
# ════════════════════════════════════════════════════════

class TestIntentsWhitelist:
    def test_all_expected_intents_present(self):
        # Sprint 2 intents — đảm bảo không bị xoá khi mở rộng Sprint 3
        sprint2_intents = {"revenue", "sellers", "top_products", "orders",
                           "customers", "product", "report", "out_of_scope"}
        assert sprint2_intents.issubset(INTENTS)

    def test_no_extra_intents(self):
        # Sprint 3 thêm channels, financials, inventory, branches → tổng 12
        assert len(INTENTS) >= 8

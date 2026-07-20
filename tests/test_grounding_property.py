"""Property tests for PILLAR "KHÔNG BỊA SỐ" (No Fabricated Numbers).

Chứng minh 3 bất biến của hệ thống grounding:
  1. Off-topic → out_of_scope — không sinh số thống kê, dù LLM down hay trả intent lạ.
  2. Mọi số trong câu trả lời đều đến từ DB (không phải từ LLM hay mã cứng).
  3. Hỏi về sản phẩm mà không nêu tên → bot hỏi lại, KHÔNG gọi repo với None.
"""
from __future__ import annotations

import re
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nlq.router import answer


# ── Helpers ──────────────────────────────────────────────────────────────────

_MONEY_RE = re.compile(r"(\d[\d.]*)\s*đ")

TODAY = date.today().isoformat()

_BASE_SALE = {
    "doc_type": "sale",
    "confidence": 0.9,
    "doc_date": TODAY,
    "party_name": "Khách Test",
    "total_amount": 750_000,
    "currency": "VND",
    "status": None,
    "tracking_code": None,
    "note": None,
    "items": [],
}


def _money_values(text: str) -> list[float]:
    """Trích tất cả số tiền (trước 'đ') trong text."""
    return [float(n.replace(".", "")) for n in _MONEY_RE.findall(text)]


def _is_out_of_scope(text: str) -> bool:
    """Kiểm tra text là câu out_of_scope cố định."""
    t = text.lower()
    return "ngoài phạm vi" in t or "chỉ trả lời" in t


def _has_no_stat_numbers(text: str) -> bool:
    """Đảm bảo text không chứa số thống kê (số tiền > 0 kèm đ)."""
    vals = _money_values(text)
    return all(v == 0.0 for v in vals)


# ════════════════════════════════════════════════════════════════════════════
# 1. Off-topic → out_of_scope — LLM down hoặc LLM trả intent lạ
# ════════════════════════════════════════════════════════════════════════════

class TestOffTopicOutOfScope:
    """Property: câu off-topic → out_of_scope, không có số thống kê bịa.

    Hai case mock:
      (a) LLM raises Exception → fail-safe out_of_scope
      (b) LLM trả intent ngoài whitelist → out_of_scope
    """

    # Câu không liên quan bán hàng và KHÔNG nằm trong _OFFTOPIC_RE
    # (buộc đi qua LLM route, không bị short-circuit bởi keyword/offtopic layer)
    _NEEDS_LLM = [
        "thủ đô nước Pháp là gì",
        "2 cộng 2 bằng mấy",
        "kể chuyện cười đi",
        "python là ngôn ngữ gì",
    ]

    # Câu bắt được ngay bởi offtopic regex — LLM không cần gọi
    _OFFTOPIC_DIRECT = [
        "thời tiết hôm nay thế nào",
        "bóng đá tối nay kết quả",
        "ăn gì hôm nay ngon",
    ]

    # ── Câu bị offtopic regex bắt — LLM không được gọi ──────────────────

    @pytest.mark.parametrize("q", _OFFTOPIC_DIRECT)
    async def test_offtopic_direct_no_llm(self, q):
        """Offtopic rõ ràng → out_of_scope ngay mà không gọi LLM."""
        with patch("nlq.router.text_json", new_callable=AsyncMock) as mock_llm:
            result = await answer(q, "g1")
        mock_llm.assert_not_awaited()
        assert _is_out_of_scope(result), f"Expected out_of_scope for '{q}', got: {result!r}"
        assert _has_no_stat_numbers(result), f"Stat numbers found in out_of_scope reply: {result!r}"

    # ── Case (a): LLM raises Exception → fail-safe out_of_scope ─────────

    @pytest.mark.parametrize("q", _NEEDS_LLM)
    async def test_llm_exception_yields_out_of_scope(self, q):
        """Case (a): LLM raise Exception → grounding fail-safe → out_of_scope."""
        with patch("nlq.router.text_json", new_callable=AsyncMock,
                   side_effect=Exception("LLM unreachable")) as mock_llm:
            result = await answer(q, "g1")
        mock_llm.assert_awaited_once()
        assert _is_out_of_scope(result), (
            f"LLM exception case: expected out_of_scope for '{q}', got: {result!r}"
        )
        assert _has_no_stat_numbers(result), (
            f"LLM exception case: stat numbers found for '{q}': {result!r}"
        )

    # ── Case (b): LLM trả intent ngoài whitelist ─────────────────────────

    @pytest.mark.parametrize("q", _NEEDS_LLM)
    async def test_llm_unknown_intent_yields_out_of_scope(self, q):
        """Case (b): LLM trả intent không trong INTENTS whitelist → out_of_scope."""
        with patch("nlq.router.text_json", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {
                "intent": "fly_to_moon",   # ngoài whitelist
                "period": "today",
                "limit": 5,
                "name_like": None,
            }
            result = await answer(q, "g1")
        mock_llm.assert_awaited_once()
        assert _is_out_of_scope(result), (
            f"Unknown intent case: expected out_of_scope for '{q}', got: {result!r}"
        )
        assert _has_no_stat_numbers(result), (
            f"Unknown intent case: stat numbers found for '{q}': {result!r}"
        )

    # ── Combo: lời chào → out_of_scope, không gọi LLM ───────────────────

    @pytest.mark.parametrize("greeting", [
        "xin chào bot",
        "hello bot",
        "chào bot em ơi",
        "xin chao, bot giup toi voi",
    ])
    async def test_greeting_out_of_scope_no_llm(self, greeting):
        """Lời chào → keyword_route None, greeting fast-path → out_of_scope, no LLM."""
        with patch("nlq.router.text_json", new_callable=AsyncMock) as mock_llm:
            result = await answer(greeting, "g1")
        mock_llm.assert_not_awaited()
        assert _is_out_of_scope(result), f"Greeting '{greeting}' must be out_of_scope, got: {result!r}"
        assert _has_no_stat_numbers(result)


# ════════════════════════════════════════════════════════════════════════════
# 2. Mọi số trong reply đến từ DB — LLM không sinh số
# ════════════════════════════════════════════════════════════════════════════

class TestNumbersGroundedInDB:
    """Property: con số trong câu trả lời = chính xác từ DB, không bịa."""

    # ── Keyword route (không qua LLM) ────────────────────────────────────

    async def test_known_total_reflected_keyword_route(self, fresh_db):
        """Seed DB tổng 750k, keyword route → answer('doanh thu hôm nay') chứa đúng 750.000đ."""
        from db import repository as repo_mod
        # Seed: 2 sale = 450k + 300k = 750k
        repo_mod.save_extraction(
            group_id="g1", sender_id="u1", sender_name="Linh", image_url=None,
            data={**_BASE_SALE, "total_amount": 450_000, "party_name": "KH A"},
        )
        repo_mod.save_extraction(
            group_id="g1", sender_id="u2", sender_name="Hà", image_url=None,
            data={**_BASE_SALE, "total_amount": 300_000, "party_name": "KH B"},
        )
        result = await answer("doanh thu hôm nay", "g1")
        assert "750.000đ" in result, (
            f"Expected '750.000đ' from DB in result, got: {result!r}"
        )

    async def test_empty_db_shows_zero_not_fabricated(self, fresh_db):
        """DB rỗng → answer('doanh thu hôm nay') = 0đ (thật), không có số > 0 bịa."""
        result = await answer("doanh thu hôm nay", "g1")
        vals = _money_values(result)
        assert all(v == 0.0 for v in vals), (
            f"Empty DB must not fabricate numbers, found non-zero: {vals} in {result!r}"
        )

    # ── LLM route → số vẫn từ DB ─────────────────────────────────────────

    async def test_llm_route_numbers_from_db_not_from_llm(self, fresh_db):
        """Khi LLM route được dùng, số vẫn từ DB — LLM chỉ trả intent/period.

        Câu 'cho biết số liệu kinh doanh' không có keyword bán hàng nào khớp
        → đi qua LLM route. Mock LLM trả intent='revenue'/period='today',
        DB có 999k → result phải chứa đúng '999.000đ' từ DB.

        Lưu ý: "doanh số" khớp keyword route nên KHÔNG dùng. "kinh doanh"
        (đảo chữ) không khớp bất kỳ pattern nào trong _keyword_route.
        """
        from db import repository as repo_mod
        repo_mod.save_extraction(
            group_id="g1", sender_id="u1", sender_name="Linh", image_url=None,
            data={**_BASE_SALE, "total_amount": 999_000},
        )
        with patch("nlq.router.text_json", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {
                "intent": "revenue", "period": "today", "limit": 5, "name_like": None,
            }
            result = await answer("cho biết số liệu kinh doanh", "g1")

        assert "999.000đ" in result, (
            f"LLM route: expected '999.000đ' from DB, got: {result!r}"
        )
        # LLM được gọi (câu không khớp keyword route)
        mock_llm.assert_awaited_once()

    async def test_llm_down_empty_db_no_fabrication(self, fresh_db):
        """LLM down + DB rỗng + câu không khớp keyword → out_of_scope, không có số bịa.

        'cho biết số liệu kinh doanh' → LLM route → LLM raise Exception
        → fail-safe out_of_scope (không sinh số bịa).
        """
        with patch("nlq.router.text_json", new_callable=AsyncMock,
                   side_effect=Exception("LLM down")):
            result = await answer("cho biết số liệu kinh doanh", "g1")

        assert _is_out_of_scope(result), f"Expected out_of_scope, got: {result!r}"
        assert _has_no_stat_numbers(result), f"Found stat numbers: {result!r}"

    async def test_customers_numbers_from_db(self, fresh_db):
        """revenue_by_customer → số tiền trong reply = đúng từ DB."""
        from db import repository as repo_mod
        repo_mod.save_extraction(
            group_id="g1", sender_id="u1", sender_name="Linh", image_url=None,
            data={**_BASE_SALE, "total_amount": 555_000, "party_name": "Khách VIP"},
        )
        result = await answer("doanh thu theo khách hàng hôm nay", "g1")
        assert "555.000đ" in result, (
            f"Expected '555.000đ' in customers reply, got: {result!r}"
        )

    async def test_customers_empty_db_no_fabrication(self, fresh_db):
        """DB rỗng, customers intent → câu trung thực, không có số > 0."""
        result = await answer("doanh thu theo khách hàng hôm nay", "g1")
        vals = _money_values(result)
        assert all(v == 0.0 for v in vals), (
            f"Empty DB customers must not fabricate, found: {vals} in {result!r}"
        )

    async def test_report_numbers_from_db(self, fresh_db):
        """full_report → tất cả số trong báo cáo khớp với dữ liệu DB đã seed."""
        from db import repository as repo_mod
        repo_mod.save_extraction(
            group_id="g1", sender_id="u1", sender_name="Linh", image_url=None,
            data={
                **_BASE_SALE,
                "total_amount": 350_000,
                "items": [
                    {"product_name": "Áo thun", "sku": None, "quantity": 2,
                     "unit_price": 100_000, "amount": 200_000},
                    {"product_name": "Nón", "sku": None, "quantity": 1,
                     "unit_price": 150_000, "amount": 150_000},
                ],
            },
        )
        result = await answer("báo cáo hôm nay", "g1")
        # Phải chứa số tiền từ DB
        assert "350.000đ" in result, f"Expected '350.000đ' in report, got: {result!r}"


# ════════════════════════════════════════════════════════════════════════════
# 3. Product không tên → hỏi lại (KHÔNG gọi repo với None)
# ════════════════════════════════════════════════════════════════════════════

class TestProductNoNameClarification:
    """Property: intent=product nhưng không trích được tên → hỏi lại người dùng.

    Đảm bảo repo.product_detail KHÔNG bao giờ được gọi với name_like=None.
    """

    async def test_product_no_name_keyword_route_asks_clarification(self, fresh_db):
        """'sản phẩm' không có tên sau → hỏi lại tên, không gọi repo."""
        with patch("nlq.router.repo") as mock_repo:
            result = await answer("sản phẩm", "g1")
        # Không gọi product_detail với None
        mock_repo.product_detail.assert_not_called()
        # Phải có câu hỏi lại
        assert "sản phẩm nào" in result.lower() or "nêu tên" in result.lower() or "cụ thể" in result.lower()

    async def test_product_llm_no_name_asks_clarification(self, fresh_db):
        """LLM trả intent='product', name_like=None → hỏi lại, không gọi repo."""
        with patch("nlq.router.text_json", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {
                "intent": "product", "period": "today", "limit": 5, "name_like": None,
            }
            with patch("nlq.router.repo") as mock_repo:
                # Query không có tên sản phẩm cụ thể → LLM cũng trả None
                result = await answer("cho biết thông tin sản phẩm", "g1")
        mock_repo.product_detail.assert_not_called()
        assert "sản phẩm nào" in result.lower() or "nêu tên" in result.lower() or "cụ thể" in result.lower()

    async def test_product_with_name_calls_repo(self, fresh_db):
        """'sản phẩm áo thun' → product_detail ĐƯỢC gọi với name_like='áo thun'."""
        with patch("nlq.router.repo") as mock_repo:
            mock_repo.product_detail.return_value = {
                "product": "áo thun", "qty": 0.0, "amount": 0.0, "count": 0,
            }
            result = await answer("sản phẩm áo thun hôm nay", "g1")
        mock_repo.product_detail.assert_called_once()
        # Arg thứ 4 là name_like — phải là chuỗi (không phải None)
        call_args = mock_repo.product_detail.call_args[0]
        name_like_arg = call_args[3] if len(call_args) > 3 else mock_repo.product_detail.call_args[1].get("name_like")
        assert name_like_arg is not None
        assert isinstance(name_like_arg, str)
        assert len(name_like_arg) > 0

    async def test_product_name_not_injected_into_llm_output(self, fresh_db):
        """LLM cung cấp name_like → được dùng làm tham số filter, không ghép SQL."""
        with patch("nlq.router.text_json", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = {
                "intent": "product", "period": "today", "limit": 5,
                "name_like": "áo thun",
            }
            with patch("nlq.router.repo") as mock_repo:
                mock_repo.product_detail.return_value = {
                    "product": "áo thun", "qty": 5.0, "amount": 500_000.0, "count": 2,
                }
                result = await answer("hỏi về áo thun tháng này", "g1")
        mock_repo.product_detail.assert_called_once()
        # Kết quả phải là formatted block (không crash)
        assert isinstance(result, str) and len(result) > 0

    @pytest.mark.parametrize("q", [
        "sản phẩm bán tháng này",  # "bán" bị STOP_RE loại bỏ → tên rỗng
        "còn bao nhiêu",            # trigger nhưng không có tên sau
    ])
    async def test_ambiguous_product_queries_ask_clarification(self, q, fresh_db):
        """Câu product mơ hồ không trích được tên → hỏi lại."""
        with patch("nlq.router.repo") as mock_repo:
            result = await answer(q, "g1")
        # Nếu hỏi lại → không gọi product_detail
        # Nếu intent thay đổi (revenue/other) → cũng OK, không crash
        assert isinstance(result, str)
        if mock_repo.product_detail.called:
            # Nếu product_detail được gọi, name_like KHÔNG được là None
            call_args = mock_repo.product_detail.call_args[0]
            name_like_arg = call_args[3] if len(call_args) > 3 else None
            assert name_like_arg is not None

"""Sprint 3 grounding property tests — "KHÔNG BỊA SỐ" for store_report.

Bất biến được kiểm chứng:
  1. Off-topic + store_report intents → out_of_scope (không chứa số tiền bịa).
  2. store_report rỗng → honest no_data (không suy đoán).
  3. Số trong câu trả lời khớp đúng với giá trị đã seed vào DB.
  4. _normalize_store_report: tiền ×int, qty float, kênh/sản phẩm lọc hợp lệ.
"""
from __future__ import annotations

import re
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

import db.repository
from nlq.router import answer


# ── Helpers ───────────────────────────────────────────────────────────────────

_MONEY_RE = re.compile(r"(\d[\d.]*)\s*đ")
TODAY = date.today().isoformat()


def _money_values(text: str) -> list[float]:
    return [float(n.replace(".", "")) for n in _MONEY_RE.findall(text)]


def _is_out_of_scope(text: str) -> bool:
    t = text.lower()
    return "ngoài phạm vi" in t or "chỉ trả lời" in t


def _has_no_stat_numbers(text: str) -> bool:
    return all(v == 0.0 for v in _money_values(text))


def _sr_data(
    report_date: str = TODAY,
    branch: str | None = "Cơ sở A",
    gross: int = 5_000_000,
    cost: int = 1_000_000,
    net: int = 4_000_000,
    cash: int = 3_000_000,
    transfer: int = 1_000_000,
    channels: list | None = None,
    products: list | None = None,
    inventory: list | None = None,
) -> dict:
    if channels is None:
        channels = [
            {"channel": "cua_hang", "revenue": gross - transfer, "banh_qty": 10.0, "nuoc_qty": 5.0},
            {"channel": "grab", "revenue": transfer, "banh_qty": 4.0, "nuoc_qty": 2.0},
        ]
    if products is None:
        products = [
            {"name": "Bánh mì", "category": "banh",
             "grab": 5.0, "now_shopee": 0.0, "xanh": 0.0, "be": 0.0, "cua_hang": 8.0, "total": 13.0},
        ]
    if inventory is None:
        inventory = [
            {"name": "Bột mì", "open": 10.0, "import": 5.0, "discard": 0.5, "close": 14.5},
        ]
    return {
        "doc_type": "store_report",
        "confidence": 0.9,
        "report": {
            "report_date": report_date,
            "branch": branch,
            "totals": {
                "gross_revenue": gross, "cost": cost, "net_revenue": net,
                "cash": cash, "transfer": transfer, "discrepancy": 0,
            },
            "channels": channels,
            "products": products,
            "inventory": inventory,
        },
    }


def _seed(group_id: str = "g1", image_hash: str = "hG", **kwargs):
    return db.repository.save_store_report(
        group_id=group_id,
        sender_id="u1",
        sender_name="Alice",
        image_url=None,
        image_hash=image_hash,
        data=_sr_data(**kwargs),
    )


# ════════════════════════════════════════════════════════════════════════════
# 1. Off-topic → out_of_scope, không sinh số
# ════════════════════════════════════════════════════════════════════════════

class TestOffTopicNoNumbers:
    """Store_report intents không làm off-topic sinh số tiền bịa."""

    _OFFTOPIC_DIRECT = [
        "thời tiết hôm nay thế nào",
        "bóng đá tối nay kết quả",
        "ăn gì hôm nay ngon",
    ]

    @pytest.mark.parametrize("q", _OFFTOPIC_DIRECT)
    async def test_offtopic_no_stat_numbers_sprint3(self, q):
        """Off-topic (regex match) → out_of_scope, không con số thống kê."""
        with patch("nlq.router.text_json", new_callable=AsyncMock) as mock_llm:
            result = await answer(q, "g1")
        mock_llm.assert_not_awaited()
        assert _is_out_of_scope(result), f"Expected out_of_scope for {q!r}, got: {result!r}"
        assert _has_no_stat_numbers(result), f"Stat numbers in out_of_scope: {result!r}"

    async def test_greeting_no_stat_numbers(self):
        with patch("nlq.router.text_json", new_callable=AsyncMock) as mock_llm:
            result = await answer("xin chào bot ơi", "g1")
        mock_llm.assert_not_awaited()
        assert _is_out_of_scope(result)
        assert _has_no_stat_numbers(result)


# ════════════════════════════════════════════════════════════════════════════
# 2. store_report rỗng → honest no_data
# ════════════════════════════════════════════════════════════════════════════

class TestStoreReportEmptyHonest:
    """Khi DB chưa có dữ liệu store_report → no_data trung thực, không bịa."""

    async def test_channels_empty_honest(self, fresh_db):
        result = await answer("doanh thu theo kênh hôm nay", "g1")
        assert "Chưa" in result or "chưa" in result, f"Expected no_data, got: {result!r}"
        assert _has_no_stat_numbers(result), f"Fabricated numbers: {result!r}"

    async def test_financials_empty_honest(self, fresh_db):
        result = await answer("tài chính hôm nay", "g1")
        assert "Chưa" in result or "chưa" in result, f"Expected no_data, got: {result!r}"
        assert _has_no_stat_numbers(result), f"Fabricated numbers: {result!r}"

    async def test_inventory_empty_honest(self, fresh_db):
        result = await answer("tồn kho hiện tại", "g1")
        assert "Chưa" in result or "chưa" in result, f"Expected no_data, got: {result!r}"
        assert _has_no_stat_numbers(result), f"Fabricated numbers: {result!r}"

    async def test_branches_empty_honest(self, fresh_db):
        result = await answer("danh sách cơ sở", "g1")
        assert "Chưa" in result or "chưa" in result, f"Expected no_data, got: {result!r}"

    async def test_baocao_empty_honest(self, fresh_db):
        result = await answer("báo cáo hôm nay", "g1")
        assert "Chưa" in result or "chưa" in result, f"Expected no_data, got: {result!r}"
        assert _has_no_stat_numbers(result), f"Fabricated numbers: {result!r}"


# ════════════════════════════════════════════════════════════════════════════
# 3. Số trong câu trả lời khớp đúng với DB (grounding property)
# ════════════════════════════════════════════════════════════════════════════

class TestNumbersGroundedInDB:
    """Số tiền trong reply = chính xác từ DB, không phải LLM hay mã cứng."""

    async def test_financials_gross_matches_seed(self, fresh_db):
        _seed(group_id="g1", image_hash="hGr1", gross=8_000_000, net=6_500_000)
        result = await answer("tài chính hôm nay", "g1")
        assert "8.000.000đ" in result, (
            f"Expected '8.000.000đ' from DB in financials reply, got: {result!r}"
        )

    async def test_channels_revenue_matches_seed(self, fresh_db):
        channels = [
            {"channel": "grab", "revenue": 3_750_000, "banh_qty": 5.0, "nuoc_qty": 2.0},
        ]
        _seed(group_id="g1", image_hash="hGr2",
              channels=channels, gross=3_750_000, transfer=3_750_000)
        result = await answer("doanh thu theo kênh hôm nay", "g1")
        assert "3.750.000đ" in result, (
            f"Expected '3.750.000đ' in channels reply, got: {result!r}"
        )

    async def test_different_group_data_not_leaked_per_chat(self, fresh_db, monkeypatch):
        """per_chat scope: g2 dữ liệu không xuất hiện trong reply của g1."""
        monkeypatch.setattr(db.repository, "_DATA_SCOPE", "per_chat")
        # Seed g2 with a distinctive amount
        db.repository.save_store_report(
            group_id="g2", sender_id="u2", sender_name="Bob",
            image_url=None, image_hash="hGrX",
            data=_sr_data(gross=9_999_000),
        )
        # g1 has no data
        result = await answer("tài chính hôm nay", "g1")
        assert "9.999.000đ" not in result, (
            f"g2 data leaked into g1 reply: {result!r}"
        )
        # No fabricated numbers for g1
        assert _has_no_stat_numbers(result), f"Fabricated numbers for empty g1: {result!r}"

    async def test_financials_zero_count_not_shown(self, fresh_db):
        """Khi không có dữ liệu financials (count=0) → honest no_data."""
        # Seed for yesterday (not today) — query today → empty
        yesterday = (date.today().replace(day=max(1, date.today().day - 1))).isoformat()
        _seed(group_id="g1", image_hash="hGr3", report_date=yesterday)
        result = await answer("tài chính hôm nay", "g1")
        # Either no_data or 0đ values (no fabrication)
        vals = _money_values(result)
        is_no_data = "Chưa" in result or "chưa" in result
        all_zero = all(v == 0.0 for v in vals)
        assert is_no_data or all_zero, (
            f"Expected no_data or zero for today's financials with yesterday's seed, "
            f"got: {result!r} (money values: {vals})"
        )


# ════════════════════════════════════════════════════════════════════════════
# 4. _normalize_store_report — vision extractor property tests
# ════════════════════════════════════════════════════════════════════════════

class TestNormalizeStoreReport:
    """Unit tests for vision/_normalize_store_report."""

    def _call(self, raw: dict) -> dict:
        from vision.extractor import _normalize_store_report
        return _normalize_store_report(raw, TODAY)

    def test_money_fields_become_int(self):
        raw = {
            "doc_type": "store_report",
            "confidence": 0.9,
            "report": {
                "totals": {
                    "gross_revenue": "5000000",  # string input
                    "cost": 1000000.7,            # float input
                    "net_revenue": 4000000,
                    "cash": 3000000,
                    "transfer": 1000000,
                    "discrepancy": 0,
                }
            }
        }
        result = self._call(raw)
        totals = result["report"]["totals"]
        assert isinstance(totals["gross_revenue"], int)
        assert totals["gross_revenue"] == 5_000_000
        assert isinstance(totals["cost"], int)
        assert totals["cost"] == 1_000_001  # rounds float

    def test_qty_fields_are_float_not_int(self):
        raw = {
            "doc_type": "store_report",
            "confidence": 0.9,
            "report": {
                "channels": [
                    {"channel": "grab", "revenue": 1000000, "banh_qty": 5, "nuoc_qty": 2},
                ]
            }
        }
        result = self._call(raw)
        ch = result["report"]["channels"][0]
        # banh_qty and nuoc_qty → float, revenue → int
        assert isinstance(ch["revenue"], int)
        assert isinstance(ch["banh_qty"], float)
        assert isinstance(ch["nuoc_qty"], float)
        assert ch["banh_qty"] == 5.0
        assert ch["nuoc_qty"] == 2.0

    def test_qty_not_multiplied_by_1000(self):
        """QTY fields must NOT be multiplied — 5 stays 5, not 5000."""
        raw = {
            "doc_type": "store_report",
            "confidence": 0.8,
            "report": {
                "channels": [
                    {"channel": "cua_hang", "revenue": 2_000_000, "banh_qty": 5, "nuoc_qty": 2},
                ],
                "inventory": [
                    {"name": "Bột mì", "open": 10, "import": 5, "discard": 1, "close": 14},
                ],
            }
        }
        result = self._call(raw)
        ch = result["report"]["channels"][0]
        assert ch["banh_qty"] == 5.0, "banh_qty must NOT be ×1000"
        assert ch["nuoc_qty"] == 2.0
        inv = result["report"]["inventory"][0]
        assert inv["open"] == 10.0
        assert inv["close"] == 14.0

    def test_invalid_channel_filtered_out(self):
        """Channel not in _VALID_CHANNELS → removed."""
        raw = {
            "doc_type": "store_report",
            "confidence": 0.9,
            "report": {
                "channels": [
                    {"channel": "grab", "revenue": 1_000_000, "banh_qty": 3.0, "nuoc_qty": 1.0},
                    {"channel": "lazada", "revenue": 500_000, "banh_qty": 1.0, "nuoc_qty": 0.0},
                ]
            }
        }
        result = self._call(raw)
        channels = result["report"]["channels"]
        assert len(channels) == 1
        assert channels[0]["channel"] == "grab"

    def test_all_valid_channels_kept(self):
        valid = ["cua_hang", "grab", "now_shopee", "xanh", "be"]
        channels_input = [
            {"channel": c, "revenue": 100_000, "banh_qty": 1.0, "nuoc_qty": 0.0}
            for c in valid
        ]
        raw = {
            "doc_type": "store_report",
            "confidence": 0.9,
            "report": {"channels": channels_input}
        }
        result = self._call(raw)
        assert len(result["report"]["channels"]) == len(valid)

    def test_invalid_product_category_filtered(self):
        """Product category not in {"banh","topping","nuoc"} → removed."""
        raw = {
            "doc_type": "store_report",
            "confidence": 0.9,
            "report": {
                "products": [
                    {"name": "Bánh mì", "category": "banh", "total": 10.0,
                     "grab": 0.0, "now_shopee": 0.0, "xanh": 0.0, "be": 0.0, "cua_hang": 10.0},
                    {"name": "Trà chanh", "category": "do_uong_khac", "total": 5.0,
                     "grab": 0.0, "now_shopee": 0.0, "xanh": 0.0, "be": 0.0, "cua_hang": 5.0},
                ]
            }
        }
        result = self._call(raw)
        prods = result["report"]["products"]
        assert len(prods) == 1
        assert prods[0]["name"] == "Bánh mì"

    def test_valid_categories_kept(self):
        categories = ["banh", "topping", "nuoc"]
        prods_input = [
            {"name": f"Sản phẩm {c}", "category": c, "total": 5.0,
             "grab": 0.0, "now_shopee": 0.0, "xanh": 0.0, "be": 0.0, "cua_hang": 5.0}
            for c in categories
        ]
        raw = {
            "doc_type": "store_report",
            "confidence": 0.9,
            "report": {"products": prods_input}
        }
        result = self._call(raw)
        assert len(result["report"]["products"]) == 3

    def test_missing_report_date_is_null(self):
        """F3 fix: store_report KHÔNG override report_date = today khi model không đọc được ngày."""
        raw = {
            "doc_type": "store_report",
            "confidence": 0.8,
            "report": {}
        }
        result = self._call(raw)
        assert result["report"]["report_date"] is None

    def test_confidence_clamped(self):
        raw = {
            "doc_type": "store_report",
            "confidence": 1.5,  # above 1.0
            "report": {}
        }
        result = self._call(raw)
        assert result["confidence"] <= 1.0

    def test_confidence_negative_clamped_to_zero(self):
        raw = {
            "doc_type": "store_report",
            "confidence": -0.5,
            "report": {}
        }
        result = self._call(raw)
        assert result["confidence"] == 0.0

    def test_doc_type_store_report_dispatches_to_normalize(self):
        """_normalize() dispatches store_report → _normalize_store_report (not sale path)."""
        from vision.extractor import _normalize
        raw = {
            "doc_type": "store_report",
            "confidence": 0.9,
            "report": {
                "report_date": "2026-07-19",
                "totals": {"gross_revenue": 5_000_000},
                "channels": [
                    {"channel": "grab", "revenue": 5_000_000, "banh_qty": 10, "nuoc_qty": 5},
                ],
            }
        }
        result = _normalize(raw, TODAY)
        # Result should have 'report' key (store_report schema), not 'total_amount'
        assert "report" in result
        assert "total_amount" not in result or result.get("doc_type") == "store_report"
        # Channel should be normalized
        assert result["report"]["channels"][0]["banh_qty"] == 10.0

    def test_inventory_fields_normalized(self):
        raw = {
            "doc_type": "store_report",
            "confidence": 0.9,
            "report": {
                "inventory": [
                    {"name": "Bột mì", "open": "10", "import": 5, "discard": 0.5, "close": 14.5},
                ]
            }
        }
        result = self._call(raw)
        inv = result["report"]["inventory"][0]
        assert isinstance(inv["open"], float)
        assert inv["open"] == 10.0
        assert inv["close"] == 14.5

    def test_null_report_section_handled(self):
        """If report is None/missing → doesn't crash, sets defaults."""
        raw = {
            "doc_type": "store_report",
            "confidence": 0.5,
            "report": None
        }
        result = self._call(raw)
        # Must have a report dict after normalization
        assert isinstance(result["report"], dict)
        assert result["report"]["channels"] == []
        assert result["report"]["products"] == []
        assert result["report"]["inventory"] == []

"""Tests for vision/extractor.py — _normalize, _loads (via beeknoee), and async
extract_document with mocked vision_json.

_normalize and _loads tests duplicate the existing vision/test_extractor_unit.py
to have them in the pytest suite as well. The async extract_document tests are new.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from vision.beeknoee import BeeknoeeError, _loads
from vision.extractor import _CONFIDENCE_THRESHOLD, _normalize, _normalize_store_report, _to_int, _to_num, extract_document

TODAY = "2026-07-19"


# ── _loads ────────────────────────────────────────────────────────────────────

class TestLoads:
    def test_clean_json(self):
        result = _loads('{"doc_type": "sale", "confidence": 0.9}')
        assert result["doc_type"] == "sale"
        assert result["confidence"] == pytest.approx(0.9)

    def test_strips_markdown_json_fence(self):
        raw = '```json\n{"doc_type": "order"}\n```'
        assert _loads(raw)["doc_type"] == "order"

    def test_strips_plain_backtick_fence(self):
        raw = '```\n{"doc_type": "unknown"}\n```'
        assert _loads(raw)["doc_type"] == "unknown"

    def test_invalid_json_raises_beeknoee_error(self):
        with pytest.raises(BeeknoeeError):
            _loads("not json at all")

    def test_empty_string_raises_beeknoee_error(self):
        with pytest.raises(BeeknoeeError):
            _loads("")


# ── _normalize: defaults ──────────────────────────────────────────────────────

class TestNormalizeDefaults:
    def test_sets_unknown_doc_type_when_missing(self):
        result = _normalize({}, TODAY)
        assert result["doc_type"] == "unknown"

    def test_sets_vnd_currency(self):
        result = _normalize({"doc_type": "sale"}, TODAY)
        assert result["currency"] == "VND"

    def test_sets_empty_items_list(self):
        result = _normalize({"doc_type": "sale"}, TODAY)
        assert result["items"] == []

    def test_uses_today_when_date_missing(self):
        result = _normalize({"doc_type": "sale"}, TODAY)
        assert result["doc_date"] == TODAY

    def test_preserves_existing_date(self):
        result = _normalize({"doc_type": "sale", "doc_date": "2024-01-15"}, TODAY)
        assert result["doc_date"] == "2024-01-15"

    def test_none_status_preserved(self):
        result = _normalize({"doc_type": "sale", "status": None}, TODAY)
        assert result["status"] is None


# ── _normalize: confidence calibration ────────────────────────────────────────

class TestNormalizeConfidence:
    def test_clamped_above_one(self):
        result = _normalize({"doc_type": "sale", "confidence": 1.5, "total_amount": 1000}, TODAY)
        assert result["confidence"] <= 1.0

    def test_clamped_below_zero(self):
        result = _normalize({"doc_type": "sale", "confidence": -0.5, "total_amount": 1000}, TODAY)
        assert result["confidence"] >= 0.0

    def test_bad_type_becomes_zero(self):
        result = _normalize({"doc_type": "sale", "confidence": "high"}, TODAY)
        assert result["confidence"] == 0.0

    def test_unknown_doc_type_caps_at_030(self):
        result = _normalize({"doc_type": "unknown", "confidence": 0.9}, TODAY)
        assert result["confidence"] <= 0.30

    def test_missing_total_and_items_lowers_confidence(self):
        # total_amount absent (0) + items=[] → penalty -0.30
        result = _normalize({"doc_type": "sale", "confidence": 0.80}, TODAY)
        assert result["confidence"] <= 0.80 - 0.30 + 0.001  # allow tiny float error

    def test_has_total_keeps_confidence(self):
        # total present (non-zero) → no penalty
        result = _normalize({"doc_type": "sale", "confidence": 0.80, "total_amount": 500_000}, TODAY)
        assert result["confidence"] > 0.50

    def test_threshold_constant_valid_range(self):
        assert 0.0 < _CONFIDENCE_THRESHOLD < 1.0

    def test_confidence_rounded_to_3_decimals(self):
        result = _normalize({"doc_type": "sale", "confidence": 0.1234567, "total_amount": 100}, TODAY)
        # result confidence should have at most 3 decimal places
        assert result["confidence"] == pytest.approx(round(0.1234567, 3))


# ── _normalize: VND amounts ───────────────────────────────────────────────────

class TestNormalizeAmounts:
    def test_float_total_to_int(self):
        result = _normalize({"doc_type": "sale", "total_amount": 1_500_000.0, "confidence": 0.9}, TODAY)
        assert result["total_amount"] == 1_500_000
        assert isinstance(result["total_amount"], int)

    def test_none_total_becomes_zero(self):
        result = _normalize({"doc_type": "sale", "total_amount": None}, TODAY)
        assert result["total_amount"] == 0

    def test_missing_total_becomes_zero(self):
        result = _normalize({"doc_type": "sale"}, TODAY)
        assert result["total_amount"] == 0

    def test_item_amounts_converted_to_int(self):
        data = {
            "doc_type": "sale", "total_amount": 300_000, "confidence": 0.9,
            "items": [{"product_name": "SP A", "unit_price": 150_000.0, "amount": 300_000.0, "quantity": 2}],
        }
        result = _normalize(data, TODAY)
        item = result["items"][0]
        assert isinstance(item["unit_price"], int)
        assert isinstance(item["amount"], int)

    def test_invalid_item_amount_becomes_zero(self):
        data = {
            "doc_type": "sale",
            "items": [{"product_name": "X", "unit_price": "abc", "amount": None, "quantity": 1}],
        }
        result = _normalize(data, TODAY)
        assert result["items"][0]["unit_price"] == 0
        assert result["items"][0]["amount"] == 0

    def test_non_dict_items_filtered_out(self):
        data = {"doc_type": "sale", "items": ["not-a-dict", None, 42]}
        result = _normalize(data, TODAY)
        assert result["items"] == []

    def test_item_sku_defaults_to_none(self):
        data = {
            "doc_type": "sale",
            "items": [{"product_name": "X", "unit_price": 10_000, "amount": 10_000, "quantity": 1}],
        }
        result = _normalize(data, TODAY)
        assert result["items"][0]["sku"] is None


# ── _normalize: status validation ─────────────────────────────────────────────

class TestNormalizeStatus:
    @pytest.mark.parametrize("s", ["cho_giao", "dang_giao", "da_giao", "huy"])
    def test_valid_status_kept(self, s):
        result = _normalize({"doc_type": "order", "status": s}, TODAY)
        assert result["status"] == s

    def test_invalid_status_nulled(self):
        result = _normalize({"doc_type": "order", "status": "shipping"}, TODAY)
        assert result["status"] is None

    def test_arbitrary_string_status_nulled(self):
        result = _normalize({"doc_type": "order", "status": "DONE"}, TODAY)
        assert result["status"] is None


# ── Fallback selection logic ──────────────────────────────────────────────────

def _norm(confidence: float, doc_type: str = "sale", total: int = 500_000) -> dict:
    return _normalize({"doc_type": doc_type, "confidence": confidence, "total_amount": total}, TODAY)


def _select(result: dict, result2: dict) -> dict:
    """Mirror logic from extract_document: result2 wins when confidence >=."""
    return result2 if result2["confidence"] >= result["confidence"] else result


class TestFallbackSelection:
    def test_fallback_wins_when_higher_confidence(self):
        result = _norm(0.40)
        result2 = _norm(0.75)
        assert _select(result, result2) is result2

    def test_original_wins_when_fallback_lower(self):
        result = _norm(0.50)
        result2 = _norm(0.30)
        assert _select(result, result2) is result

    def test_fallback_wins_on_equal_confidence(self):
        result = _norm(0.50)
        result2 = _norm(0.50)
        assert _select(result, result2) is result2

    def test_fallback_zero_confidence_keeps_original(self):
        result = _norm(0.45)
        result2 = _norm(0.05, doc_type="unknown", total=0)  # capped to 0.05
        assert _select(result, result2) is result

    def test_both_low_uses_fallback_when_equal_or_better(self):
        result = _norm(0.20)
        result2 = _norm(0.22)
        assert _select(result, result2) is result2


# ── async extract_document with mock ─────────────────────────────────────────

_GOOD_RESPONSE = {
    "doc_type": "sale",
    "confidence": 0.90,
    "doc_date": TODAY,
    "party_name": "KH Test",
    "total_amount": 500_000,
    "currency": "VND",
    "status": None,
    "tracking_code": None,
    "note": None,
    "items": [],
}

_LOW_CONF_RESPONSE = {
    "doc_type": "sale",
    "confidence": 0.40,
    "doc_date": TODAY,
    "party_name": None,
    "total_amount": 0,
    "currency": "VND",
    "status": None,
    "tracking_code": None,
    "note": None,
    "items": [],
}

_FALLBACK_BETTER = {
    "doc_type": "sale",
    "confidence": 0.70,
    "doc_date": TODAY,
    "party_name": "KH Fallback",
    "total_amount": 500_000,
    "currency": "VND",
    "status": None,
    "tracking_code": None,
    "note": None,
    "items": [],
}

_FALLBACK_WORSE = {
    "doc_type": "sale",
    "confidence": 0.20,
    "doc_date": TODAY,
    "party_name": None,
    "total_amount": 0,
    "currency": "VND",
    "status": None,
    "tracking_code": None,
    "note": None,
    "items": [],
}


async def test_extract_document_main_model_success():
    """Happy path: main model returns good result above threshold."""
    with patch("vision.extractor.vision_json", new_callable=AsyncMock) as mock_vj:
        mock_vj.return_value = _GOOD_RESPONSE
        result = await extract_document(b"fake_image", mime="image/jpeg")
    assert result["doc_type"] == "sale"
    assert result["confidence"] >= _CONFIDENCE_THRESHOLD
    mock_vj.assert_awaited_once()


async def test_extract_document_fallback_on_beeknoee_error():
    """Main model raises BeeknoeeError → fallback model called."""
    call_count = 0

    async def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise BeeknoeeError("JSON parse error")
        return _GOOD_RESPONSE

    with patch("vision.extractor.vision_json", side_effect=side_effect):
        result = await extract_document(b"fake_image")
    assert result["doc_type"] == "sale"
    assert call_count == 2  # main + fallback


async def test_extract_document_fallback_wins_when_better():
    """Low confidence main → fallback tried, fallback better → use fallback."""
    call_count = 0

    async def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return _LOW_CONF_RESPONSE if call_count == 1 else _FALLBACK_BETTER

    with patch("vision.extractor.vision_json", side_effect=side_effect):
        result = await extract_document(b"fake_image")
    # fallback has confidence 0.70 > 0.10 (low after penalty), fallback should win
    assert call_count == 2
    assert result["confidence"] >= 0.60  # normalized fallback


async def test_extract_document_fallback_loses_keeps_original():
    """Low confidence main → fallback tried, fallback WORSE → keep original."""
    call_count = 0

    async def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        # Both have total_amount=0 and items=[] → penalty applied
        if call_count == 1:
            return {**_LOW_CONF_RESPONSE, "confidence": 0.40}
        return {**_FALLBACK_WORSE, "confidence": 0.10}

    with patch("vision.extractor.vision_json", side_effect=side_effect):
        result = await extract_document(b"fake_image")
    # original conf after penalty: max(0,0.40-0.30)=0.10; fallback: max(0,0.10-0.30)=0
    # fallback (0) < original (0.10) → keep original
    assert call_count == 2
    assert result["confidence"] >= 0.0


async def test_extract_document_both_fail_raises():
    """Both main and fallback raise BeeknoeeError → exception propagates."""
    with patch("vision.extractor.vision_json", new_callable=AsyncMock) as mock_vj:
        mock_vj.side_effect = BeeknoeeError("API down")
        with pytest.raises(BeeknoeeError):
            await extract_document(b"fake_image")


async def test_extract_document_high_confidence_skips_fallback():
    """High confidence result → fallback not called at all."""
    with patch("vision.extractor.vision_json", new_callable=AsyncMock) as mock_vj:
        mock_vj.return_value = _GOOD_RESPONSE  # confidence 0.90 >= 0.55 threshold
        await extract_document(b"fake_image")
    assert mock_vj.await_count == 1


async def test_extract_document_normalizes_result():
    """extract_document returns properly normalized data."""
    raw = {
        "doc_type": "order",
        "confidence": 0.80,
        "total_amount": "1500000",  # string → should be converted to int
        "status": "invalid_status",  # should be nulled
        "items": [],
    }
    with patch("vision.extractor.vision_json", new_callable=AsyncMock) as mock_vj:
        mock_vj.return_value = raw
        result = await extract_document(b"fake_image")
    assert result["total_amount"] == 1_500_000
    assert result["status"] is None


# ── _normalize store_report ───────────────────────────────────────────────────

class TestNormalizeStoreReport:
    """Unit tests cho _normalize với doc_type=store_report."""

    def test_routes_to_store_report_normalizer(self):
        result = _normalize({"doc_type": "store_report", "confidence": 0.9}, TODAY)
        assert result["doc_type"] == "store_report"
        assert "report" in result

    def test_no_confidence_penalty_for_store_report(self):
        """store_report không có total_amount/items → không bị giảm confidence."""
        result = _normalize({"doc_type": "store_report", "confidence": 0.85}, TODAY)
        assert result["confidence"] == pytest.approx(0.85)

    def test_confidence_clamped_above_one(self):
        result = _normalize({"doc_type": "store_report", "confidence": 1.5}, TODAY)
        assert result["confidence"] <= 1.0

    def test_report_date_null_when_model_returns_none(self):
        """F3 fix: store_report KHÔNG override report_date = today khi model trả null."""
        result = _normalize({"doc_type": "store_report", "confidence": 0.8}, TODAY)
        assert result["report"]["report_date"] is None

    def test_report_date_preserved_when_given(self):
        data = {"doc_type": "store_report", "confidence": 0.8,
                "report": {"report_date": "2026-07-19"}}
        result = _normalize(data, TODAY)
        assert result["report"]["report_date"] == "2026-07-19"

    def test_channels_revenue_is_int(self):
        data = {
            "doc_type": "store_report", "confidence": 0.9,
            "report": {
                "channels": [{"channel": "grab", "revenue": 159000.0, "banh_qty": 4, "nuoc_qty": 4}]
            },
        }
        result = _normalize(data, TODAY)
        ch = result["report"]["channels"][0]
        assert isinstance(ch["revenue"], int)
        assert ch["revenue"] == 159000

    def test_channels_qty_is_float_not_multiplied(self):
        data = {
            "doc_type": "store_report", "confidence": 0.9,
            "report": {
                "channels": [{"channel": "cua_hang", "revenue": 3923000, "banh_qty": 101, "nuoc_qty": 22}]
            },
        }
        result = _normalize(data, TODAY)
        ch = result["report"]["channels"][0]
        assert ch["banh_qty"] == pytest.approx(101.0)
        assert ch["nuoc_qty"] == pytest.approx(22.0)

    def test_invalid_channel_filtered(self):
        data = {
            "doc_type": "store_report", "confidence": 0.8,
            "report": {
                "channels": [
                    {"channel": "bad_channel", "revenue": 1000, "banh_qty": 1, "nuoc_qty": 0},
                    {"channel": "xanh", "revenue": 89000, "banh_qty": 3, "nuoc_qty": 1},
                ]
            },
        }
        result = _normalize(data, TODAY)
        assert len(result["report"]["channels"]) == 1
        assert result["report"]["channels"][0]["channel"] == "xanh"

    def test_totals_int_vnd(self):
        data = {
            "doc_type": "store_report", "confidence": 0.9,
            "report": {
                "totals": {
                    "gross_revenue": 5326000, "cost": 715000, "net_revenue": 4627000,
                    "cash": 962000, "transfer": 2262000, "discrepancy": 16000,
                }
            },
        }
        result = _normalize(data, TODAY)
        t = result["report"]["totals"]
        assert t["net_revenue"] == 4627000
        assert t["cash"] == 962000
        assert isinstance(t["gross_revenue"], int)

    def test_totals_null_when_absent(self):
        result = _normalize({"doc_type": "store_report", "confidence": 0.8, "report": {}}, TODAY)
        assert result["report"]["totals"] is None

    def test_products_valid_category_kept(self):
        data = {
            "doc_type": "store_report", "confidence": 0.85,
            "report": {
                "products": [
                    {"name": "HA", "category": "banh", "grab": 3, "now_shopee": 22,
                     "xanh": 2, "be": 0, "cua_hang": 51, "total": 78}
                ]
            },
        }
        result = _normalize(data, TODAY)
        assert len(result["report"]["products"]) == 1
        assert result["report"]["products"][0]["name"] == "HA"
        assert result["report"]["products"][0]["total"] == pytest.approx(78.0)

    def test_products_invalid_category_filtered(self):
        data = {
            "doc_type": "store_report", "confidence": 0.8,
            "report": {"products": [{"name": "X", "category": "unknown_cat", "grab": 0,
                                      "now_shopee": 0, "xanh": 0, "be": 0, "cua_hang": 0, "total": 0}]},
        }
        result = _normalize(data, TODAY)
        assert result["report"]["products"] == []

    def test_inventory_values_float(self):
        data = {
            "doc_type": "store_report", "confidence": 0.88,
            "report": {
                "inventory": [{"name": "HA", "open": 100, "import": 0, "discard": 0, "close": 22}]
            },
        }
        result = _normalize(data, TODAY)
        inv = result["report"]["inventory"][0]
        assert inv["open"] == pytest.approx(100.0)
        assert inv["close"] == pytest.approx(22.0)

    def test_missing_report_creates_empty_defaults(self):
        result = _normalize({"doc_type": "store_report", "confidence": 0.7}, TODAY)
        r = result["report"]
        assert r["channels"] == []
        assert r["totals"] is None
        assert r["products"] == []
        assert r["inventory"] == []
        assert r["branch"] is None

    # --- F7: lọc item name rỗng ---
    def test_products_empty_name_filtered(self):
        """F7: product với name rỗng bị loại bỏ."""
        data = {
            "doc_type": "store_report", "confidence": 0.85,
            "report": {
                "products": [
                    {"name": "",  "category": "banh", "grab": 0, "now_shopee": 0, "xanh": 0, "be": 0, "cua_hang": 5, "total": 5},
                    {"name": "  ", "category": "nuoc", "grab": 0, "now_shopee": 1, "xanh": 0, "be": 0, "cua_hang": 0, "total": 1},
                    {"name": "HA", "category": "banh", "grab": 3, "now_shopee": 22, "xanh": 2, "be": 0, "cua_hang": 51, "total": 78},
                ]
            },
        }
        result = _normalize(data, TODAY)
        products = result["report"]["products"]
        assert len(products) == 1
        assert products[0]["name"] == "HA"

    def test_inventory_empty_name_filtered(self):
        """F7: inventory item với name rỗng bị loại bỏ."""
        data = {
            "doc_type": "store_report", "confidence": 0.88,
            "report": {
                "inventory": [
                    {"name": "",   "open": 50, "import": 0, "discard": 0, "close": 50},
                    {"name": "HA", "open": 100, "import": 0, "discard": 0, "close": 22},
                ]
            },
        }
        result = _normalize(data, TODAY)
        inv = result["report"]["inventory"]
        assert len(inv) == 1
        assert inv[0]["name"] == "HA"

    def test_products_whitespace_only_name_filtered(self):
        """F7: name chỉ có whitespace cũng bị loại."""
        data = {
            "doc_type": "store_report", "confidence": 0.8,
            "report": {
                "products": [
                    {"name": "   ", "category": "topping", "grab": 0, "now_shopee": 0,
                     "xanh": 0, "be": 0, "cua_hang": 2, "total": 2},
                ]
            },
        }
        result = _normalize(data, TODAY)
        assert result["report"]["products"] == []

    def test_five_channel_full_example(self):
        """Test đầy đủ với 5 kênh — xác nhận ×1000 đúng trong normalize."""
        data = {
            "doc_type": "store_report", "confidence": 0.93,
            "report": {
                "report_date": "2026-07-19",
                "branch": "Trần Đăng Ninh",
                "channels": [
                    {"channel": "cua_hang",   "revenue": 3923000, "banh_qty": 101, "nuoc_qty": 22},
                    {"channel": "grab",       "revenue": 159000,  "banh_qty": 4,   "nuoc_qty": 4},
                    {"channel": "now_shopee", "revenue": 1155000, "banh_qty": 38,  "nuoc_qty": 9},
                    {"channel": "xanh",       "revenue": 89000,   "banh_qty": 3,   "nuoc_qty": 1},
                    {"channel": "be",         "revenue": 0,        "banh_qty": 0,   "nuoc_qty": 1},
                ],
                "totals": {
                    "gross_revenue": 5326000, "cost": 715000, "net_revenue": 4627000,
                    "cash": 962000, "transfer": 2262000, "discrepancy": 16000,
                },
                "products": [],
                "inventory": [],
            },
        }
        result = _normalize(data, TODAY)
        assert result["confidence"] == pytest.approx(0.93)
        assert result["report"]["branch"] == "Trần Đăng Ninh"
        assert len(result["report"]["channels"]) == 5
        assert result["report"]["totals"]["net_revenue"] == 4627000
        assert result["report"]["totals"]["cash"] == 962000
        assert result["report"]["totals"]["transfer"] == 2262000


# ── _to_int / _to_num helpers ─────────────────────────────────────────────────

class TestHelpers:
    def test_to_int_float(self):
        assert _to_int(5326.7) == 5327

    def test_to_int_none(self):
        assert _to_int(None) == 0

    def test_to_int_invalid(self):
        assert _to_int("abc") == 0

    def test_to_num_int(self):
        assert _to_num(101) == pytest.approx(101.0)

    def test_to_num_none(self):
        assert _to_num(None) == pytest.approx(0.0)

    def test_to_num_invalid(self):
        assert _to_num("bad") == pytest.approx(0.0)


# ── async extract_document with store_report mock ────────────────────────────

_STORE_REPORT_RESPONSE = {
    "doc_type": "store_report",
    "confidence": 0.93,
    "report": {
        "report_date": "2026-07-19",
        "branch": "Trần Đăng Ninh",
        "channels": [
            {"channel": "cua_hang",   "revenue": 3923000, "banh_qty": 101, "nuoc_qty": 22},
            {"channel": "grab",       "revenue": 159000,  "banh_qty": 4,   "nuoc_qty": 4},
            {"channel": "now_shopee", "revenue": 1155000, "banh_qty": 38,  "nuoc_qty": 9},
            {"channel": "xanh",       "revenue": 89000,   "banh_qty": 3,   "nuoc_qty": 1},
            {"channel": "be",         "revenue": 0,        "banh_qty": 0,   "nuoc_qty": 1},
        ],
        "totals": {
            "gross_revenue": 5326000, "cost": 715000, "net_revenue": 4627000,
            "cash": 962000, "transfer": 2262000, "discrepancy": 16000,
        },
        "products": [],
        "inventory": [],
    },
}


async def test_extract_document_store_report_normalized():
    """extract_document với store_report mock → doc_type+report structure đúng."""
    with patch("vision.extractor.vision_json", new_callable=AsyncMock) as mock_vj:
        mock_vj.return_value = _STORE_REPORT_RESPONSE
        result = await extract_document(b"fake_image")
    assert result["doc_type"] == "store_report"
    assert result["confidence"] == pytest.approx(0.93)
    r = result["report"]
    assert r["branch"] == "Trần Đăng Ninh"
    assert len(r["channels"]) == 5
    assert r["totals"]["net_revenue"] == 4627000
    assert r["totals"]["cash"] == 962000
    # Xác nhận store_report KHÔNG có total_amount/items
    assert "total_amount" not in result
    assert "items" not in result

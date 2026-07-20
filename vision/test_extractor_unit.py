"""Unit tests cho logic nội bộ của vision layer — KHÔNG cần mạng hay API key.

Chạy:
    .venv/bin/python -m pytest vision/test_extractor_unit.py -v
hoặc:
    .venv/bin/python vision/test_extractor_unit.py
"""
from __future__ import annotations

import sys
import types
import unittest

# ---------------------------------------------------------------------------
# Stub config.settings để không cần .env khi chạy unit test
# ---------------------------------------------------------------------------
_fake_settings = types.SimpleNamespace(
    beeknoee_api_key="test-key",
    beeknoee_base_url="https://example.com/v1",
    vision_model="gemini-2.5-flash-lite",
    nlq_model="gemini-2.5-flash-lite",
)

_fake_config = types.ModuleType("config")
_fake_config_settings = types.ModuleType("config.settings")
_fake_config_settings.settings = _fake_settings
sys.modules.setdefault("config", _fake_config)
sys.modules.setdefault("config.settings", _fake_config_settings)

# ---------------------------------------------------------------------------
# Import modules under test (chỉ sau khi stub sẵn sàng)
# ---------------------------------------------------------------------------
from vision.beeknoee import BeeknoeeError, _loads  # noqa: E402
from vision.extractor import _CONFIDENCE_THRESHOLD, _normalize, _normalize_store_report, _to_int, _to_num  # noqa: E402


# ---------------------------------------------------------------------------
# Tests: _loads
# ---------------------------------------------------------------------------
class TestLoads(unittest.TestCase):
    def test_clean_json(self):
        result = _loads('{"doc_type": "sale", "confidence": 0.9}')
        self.assertEqual(result["doc_type"], "sale")
        self.assertAlmostEqual(result["confidence"], 0.9)

    def test_strips_markdown_fence(self):
        raw = "```json\n{\"doc_type\": \"order\"}\n```"
        result = _loads(raw)
        self.assertEqual(result["doc_type"], "order")

    def test_strips_plain_fence(self):
        raw = "```\n{\"doc_type\": \"unknown\"}\n```"
        result = _loads(raw)
        self.assertEqual(result["doc_type"], "unknown")

    def test_invalid_json_raises(self):
        with self.assertRaises(BeeknoeeError):
            _loads("not json at all")

    def test_empty_string_raises(self):
        with self.assertRaises(BeeknoeeError):
            _loads("")


# ---------------------------------------------------------------------------
# Tests: _normalize — giá trị mặc định
# ---------------------------------------------------------------------------
class TestNormalizeDefaults(unittest.TestCase):
    TODAY = "2026-07-19"

    def _run(self, data: dict) -> dict:
        return _normalize(data, self.TODAY)

    def test_sets_default_doc_type(self):
        result = self._run({})
        self.assertEqual(result["doc_type"], "unknown")

    def test_sets_default_currency(self):
        result = self._run({"doc_type": "sale"})
        self.assertEqual(result["currency"], "VND")

    def test_sets_default_items(self):
        result = self._run({"doc_type": "sale"})
        self.assertEqual(result["items"], [])

    def test_uses_today_when_no_date(self):
        result = self._run({"doc_type": "sale"})
        self.assertEqual(result["doc_date"], self.TODAY)

    def test_keeps_existing_date(self):
        result = self._run({"doc_type": "sale", "doc_date": "2024-01-15"})
        self.assertEqual(result["doc_date"], "2024-01-15")


# ---------------------------------------------------------------------------
# Tests: _normalize — chuẩn hoá số tiền
# ---------------------------------------------------------------------------
class TestNormalizeAmounts(unittest.TestCase):
    TODAY = "2026-07-19"

    def _run(self, data: dict) -> dict:
        return _normalize(data, self.TODAY)

    def test_total_amount_float_to_int(self):
        result = self._run({"doc_type": "sale", "total_amount": 1500000.0})
        self.assertEqual(result["total_amount"], 1500000)
        self.assertIsInstance(result["total_amount"], int)

    def test_total_amount_none_becomes_zero(self):
        result = self._run({"doc_type": "sale", "total_amount": None})
        self.assertEqual(result["total_amount"], 0)

    def test_total_amount_missing_becomes_zero(self):
        result = self._run({"doc_type": "sale"})
        self.assertEqual(result["total_amount"], 0)

    def test_item_amounts_converted(self):
        data = {
            "doc_type": "sale",
            "total_amount": 300000,
            "items": [
                {"product_name": "SP A", "unit_price": 150000.5, "amount": 300000.9, "quantity": 2}
            ],
        }
        result = self._run(data)
        item = result["items"][0]
        # Python banker's rounding: round(150000.5) = 150000 (even), round(300000.9) = 300001
        self.assertEqual(item["unit_price"], 150000)
        self.assertEqual(item["amount"], 300001)

    def test_item_with_invalid_amount_becomes_zero(self):
        data = {
            "doc_type": "sale",
            "items": [{"product_name": "X", "unit_price": "abc", "amount": None, "quantity": 1}],
        }
        result = self._run(data)
        item = result["items"][0]
        self.assertEqual(item["unit_price"], 0)
        self.assertEqual(item["amount"], 0)

    def test_non_dict_items_filtered_out(self):
        data = {"doc_type": "sale", "items": ["not a dict", None, 42]}
        result = self._run(data)
        self.assertEqual(result["items"], [])

    def test_item_sku_defaulted_to_none(self):
        data = {
            "doc_type": "sale",
            "items": [{"product_name": "X", "unit_price": 10000, "amount": 10000, "quantity": 1}],
        }
        result = self._run(data)
        self.assertIsNone(result["items"][0]["sku"])


# ---------------------------------------------------------------------------
# Tests: _normalize — confidence calibration
# ---------------------------------------------------------------------------
class TestNormalizeConfidence(unittest.TestCase):
    TODAY = "2026-07-19"

    def _run(self, data: dict) -> dict:
        return _normalize(data, self.TODAY)

    def test_confidence_clamped_above_one(self):
        result = self._run({"doc_type": "sale", "confidence": 1.5})
        self.assertLessEqual(result["confidence"], 1.0)

    def test_confidence_clamped_below_zero(self):
        result = self._run({"doc_type": "sale", "confidence": -0.5})
        self.assertGreaterEqual(result["confidence"], 0.0)

    def test_confidence_bad_type_becomes_zero(self):
        result = self._run({"doc_type": "sale", "confidence": "high"})
        self.assertEqual(result["confidence"], 0.0)

    def test_unknown_doc_type_caps_confidence(self):
        result = self._run({"doc_type": "unknown", "confidence": 0.9})
        self.assertLessEqual(result["confidence"], 0.30)

    def test_missing_total_and_items_lowers_confidence(self):
        result = self._run({"doc_type": "sale", "confidence": 0.8})
        # total_amount absent (defaults 0) + items absent → penalty -0.30
        self.assertLessEqual(result["confidence"], 0.80 - 0.30 + 0.001)

    def test_has_total_keeps_confidence(self):
        result = self._run({"doc_type": "sale", "confidence": 0.8, "total_amount": 500000})
        # total present (non-zero), items empty — penalty only if BOTH missing
        self.assertGreater(result["confidence"], 0.50)

    def test_threshold_constant_in_range(self):
        self.assertGreater(_CONFIDENCE_THRESHOLD, 0.0)
        self.assertLess(_CONFIDENCE_THRESHOLD, 1.0)


# ---------------------------------------------------------------------------
# Tests: _normalize — status validation
# ---------------------------------------------------------------------------
class TestNormalizeStatus(unittest.TestCase):
    TODAY = "2026-07-19"

    def _run(self, data: dict) -> dict:
        return _normalize(data, self.TODAY)

    def test_valid_status_kept(self):
        for s in ("cho_giao", "dang_giao", "da_giao", "huy"):
            result = self._run({"doc_type": "order", "status": s})
            self.assertEqual(result["status"], s, f"status '{s}' should be kept")

    def test_invalid_status_nulled(self):
        result = self._run({"doc_type": "order", "status": "unknown_status"})
        self.assertIsNone(result["status"])

    def test_none_status_kept(self):
        result = self._run({"doc_type": "sale", "status": None})
        self.assertIsNone(result["status"])


# ---------------------------------------------------------------------------
# Tests: fallback selection logic (MAJOR-3 fix)
# Logic thuần tuý — mô phỏng việc chọn result2 hay result gốc dựa trên confidence
# ---------------------------------------------------------------------------
class TestFallbackSelection(unittest.TestCase):
    """Kiểm tra logic chọn kết quả fallback — không cần network / API call.

    Mô phỏng hành vi của đoạn code trong extract_document:
        return result2 if result2["confidence"] >= result["confidence"] else result
    """

    TODAY = "2026-07-19"

    def _norm(self, confidence: float, doc_type: str = "sale", total: int = 500000) -> dict:
        """Tạo một normalized result với confidence mong muốn."""
        return _normalize(
            {"doc_type": doc_type, "confidence": confidence, "total_amount": total},
            self.TODAY,
        )

    def _select(self, result: dict, result2: dict) -> dict:
        """Ánh xạ logic chọn kết quả trong extract_document."""
        return result2 if result2["confidence"] >= result["confidence"] else result

    def test_fallback_wins_when_higher_confidence(self):
        """result2 có confidence cao hơn → dùng result2."""
        result = self._norm(0.40)
        result2 = self._norm(0.75)
        chosen = self._select(result, result2)
        self.assertIs(chosen, result2)
        self.assertAlmostEqual(chosen["confidence"], result2["confidence"])

    def test_original_wins_when_fallback_lower(self):
        """result2 có confidence thấp hơn → giữ result gốc."""
        result = self._norm(0.50)
        result2 = self._norm(0.30)
        chosen = self._select(result, result2)
        self.assertIs(chosen, result)
        self.assertAlmostEqual(chosen["confidence"], result["confidence"])

    def test_fallback_wins_on_equal_confidence(self):
        """Confidence bằng nhau → dùng result2 (tiebreak về phía fallback model mạnh hơn)."""
        result = self._norm(0.50)
        result2 = self._norm(0.50)
        chosen = self._select(result, result2)
        self.assertIs(chosen, result2)

    def test_fallback_zero_confidence_keeps_original(self):
        """Fallback trả về confidence 0 (doc_type=unknown) → giữ result gốc."""
        result = self._norm(0.45)
        # unknown doc_type bị cap ở 0.30 bởi _normalize
        result2 = self._norm(0.05, doc_type="unknown", total=0)
        chosen = self._select(result, result2)
        self.assertIs(chosen, result)

    def test_both_low_confidence_uses_fallback_when_equal_or_better(self):
        """Cả hai đều thấp — chọn theo rule >= (fallback ≥ original)."""
        result = self._norm(0.20)
        result2 = self._norm(0.22)
        chosen = self._select(result, result2)
        self.assertIs(chosen, result2)


# ---------------------------------------------------------------------------
# Tests: _normalize_store_report
# ---------------------------------------------------------------------------
class TestNormalizeStoreReport(unittest.TestCase):
    TODAY = "2026-07-20"

    def _run(self, data: dict) -> dict:
        return _normalize(data, self.TODAY)

    # --- doc_type routing ---
    def test_store_report_routed_to_own_normalizer(self):
        result = self._run({"doc_type": "store_report", "confidence": 0.9})
        self.assertEqual(result["doc_type"], "store_report")
        self.assertIn("report", result)

    def test_store_report_no_confidence_penalty(self):
        """store_report không có total_amount/items → confidence KHÔNG bị giảm."""
        result = self._run({"doc_type": "store_report", "confidence": 0.85})
        self.assertAlmostEqual(result["confidence"], 0.85)

    def test_confidence_clamped(self):
        result = self._run({"doc_type": "store_report", "confidence": 1.5})
        self.assertLessEqual(result["confidence"], 1.0)

    # --- report.report_date ---
    def test_report_date_null_when_model_returns_none(self):
        """F3 fix: store_report KHÔNG override report_date = today khi model trả null."""
        result = self._run({"doc_type": "store_report", "confidence": 0.8})
        self.assertIsNone(result["report"]["report_date"])

    def test_report_date_preserved(self):
        data = {
            "doc_type": "store_report", "confidence": 0.8,
            "report": {"report_date": "2026-07-19"},
        }
        result = self._run(data)
        self.assertEqual(result["report"]["report_date"], "2026-07-19")

    # --- channels ---
    def test_channels_revenue_int_vnd(self):
        data = {
            "doc_type": "store_report", "confidence": 0.9,
            "report": {
                "channels": [
                    {"channel": "grab", "revenue": 159000, "banh_qty": 4, "nuoc_qty": 4}
                ]
            },
        }
        result = self._run(data)
        ch = result["report"]["channels"][0]
        self.assertIsInstance(ch["revenue"], int)
        self.assertEqual(ch["revenue"], 159000)

    def test_channels_qty_not_multiplied(self):
        """banh_qty và nuoc_qty là số lượng, KHÔNG nhân 1000."""
        data = {
            "doc_type": "store_report", "confidence": 0.9,
            "report": {
                "channels": [
                    {"channel": "cua_hang", "revenue": 3923000, "banh_qty": 101, "nuoc_qty": 22}
                ]
            },
        }
        result = self._run(data)
        ch = result["report"]["channels"][0]
        self.assertEqual(ch["banh_qty"], 101.0)
        self.assertEqual(ch["nuoc_qty"], 22.0)

    def test_invalid_channel_filtered_out(self):
        data = {
            "doc_type": "store_report", "confidence": 0.8,
            "report": {
                "channels": [
                    {"channel": "invalid_channel", "revenue": 100000, "banh_qty": 1, "nuoc_qty": 0},
                    {"channel": "grab", "revenue": 200000, "banh_qty": 5, "nuoc_qty": 2},
                ]
            },
        }
        result = self._run(data)
        self.assertEqual(len(result["report"]["channels"]), 1)
        self.assertEqual(result["report"]["channels"][0]["channel"], "grab")

    def test_empty_channels_stays_empty(self):
        data = {"doc_type": "store_report", "confidence": 0.8, "report": {"channels": []}}
        result = self._run(data)
        self.assertEqual(result["report"]["channels"], [])

    # --- totals ---
    def test_totals_normalized_to_int(self):
        data = {
            "doc_type": "store_report", "confidence": 0.9,
            "report": {
                "totals": {
                    "gross_revenue": 5326000.0, "cost": 715000.0, "net_revenue": 4627000.0,
                    "cash": 962000.0, "transfer": 2262000.0, "discrepancy": 16000.0,
                }
            },
        }
        result = self._run(data)
        t = result["report"]["totals"]
        self.assertEqual(t["gross_revenue"], 5326000)
        self.assertEqual(t["net_revenue"], 4627000)
        self.assertEqual(t["cash"], 962000)
        self.assertIsInstance(t["net_revenue"], int)

    def test_totals_null_when_missing(self):
        data = {"doc_type": "store_report", "confidence": 0.8, "report": {}}
        result = self._run(data)
        self.assertIsNone(result["report"]["totals"])

    # --- products ---
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
        result = self._run(data)
        self.assertEqual(len(result["report"]["products"]), 1)
        p = result["report"]["products"][0]
        self.assertEqual(p["name"], "HA")
        self.assertEqual(p["category"], "banh")
        self.assertEqual(p["total"], 78.0)

    def test_products_invalid_category_filtered(self):
        data = {
            "doc_type": "store_report", "confidence": 0.8,
            "report": {
                "products": [
                    {"name": "X", "category": "invalid", "grab": 0, "now_shopee": 0,
                     "xanh": 0, "be": 0, "cua_hang": 0, "total": 0}
                ]
            },
        }
        result = self._run(data)
        self.assertEqual(result["report"]["products"], [])

    # --- inventory ---
    def test_inventory_values_as_float(self):
        data = {
            "doc_type": "store_report", "confidence": 0.88,
            "report": {
                "inventory": [
                    {"name": "HA", "open": 100, "import": 0, "discard": 0, "close": 22}
                ]
            },
        }
        result = self._run(data)
        inv = result["report"]["inventory"][0]
        self.assertEqual(inv["name"], "HA")
        self.assertEqual(inv["open"], 100.0)
        self.assertEqual(inv["close"], 22.0)

    def test_inventory_empty_stays_empty(self):
        data = {"doc_type": "store_report", "confidence": 0.8, "report": {"inventory": []}}
        result = self._run(data)
        self.assertEqual(result["report"]["inventory"], [])

    def test_products_empty_name_filtered(self):
        """F7: product với name rỗng bị loại bỏ."""
        data = {
            "doc_type": "store_report", "confidence": 0.85,
            "report": {
                "products": [
                    {"name": "",   "category": "banh", "grab": 0, "now_shopee": 0, "xanh": 0, "be": 0, "cua_hang": 3, "total": 3},
                    {"name": "HA", "category": "banh", "grab": 3, "now_shopee": 22, "xanh": 2, "be": 0, "cua_hang": 51, "total": 78},
                ]
            },
        }
        result = self._run(data)
        products = result["report"]["products"]
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["name"], "HA")

    def test_inventory_empty_name_filtered(self):
        """F7: inventory item với name rỗng bị loại bỏ."""
        data = {
            "doc_type": "store_report", "confidence": 0.88,
            "report": {
                "inventory": [
                    {"name": "  ", "open": 50, "import": 0, "discard": 0, "close": 50},
                    {"name": "G",  "open": 30, "import": 0, "discard": 0, "close": 15},
                ]
            },
        }
        result = self._run(data)
        inv = result["report"]["inventory"]
        self.assertEqual(len(inv), 1)
        self.assertEqual(inv[0]["name"], "G")

    def test_non_dict_inventory_filtered(self):
        data = {
            "doc_type": "store_report", "confidence": 0.8,
            "report": {"inventory": ["not-a-dict", None, {"name": "TQ", "open": 30, "import": 0, "discard": 0, "close": 19}]}
        }
        result = self._run(data)
        self.assertEqual(len(result["report"]["inventory"]), 1)

    # --- missing report section ---
    def test_missing_report_section_creates_defaults(self):
        result = self._run({"doc_type": "store_report", "confidence": 0.7})
        r = result["report"]
        self.assertEqual(r["channels"], [])
        self.assertIsNone(r["totals"])
        self.assertEqual(r["products"], [])
        self.assertEqual(r["inventory"], [])
        self.assertIsNone(r["branch"])


class TestToHelpers(unittest.TestCase):
    def test_to_int_normal(self):
        self.assertEqual(_to_int(5326.7), 5327)
        self.assertEqual(_to_int(0), 0)
        self.assertEqual(_to_int(None), 0)
        self.assertEqual(_to_int("abc"), 0)

    def test_to_num_normal(self):
        self.assertEqual(_to_num(101), 101.0)
        self.assertEqual(_to_num(None), 0.0)
        self.assertEqual(_to_num("bad"), 0.0)


if __name__ == "__main__":
    unittest.main()

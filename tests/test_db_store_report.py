"""Tests for Sprint 3 store_report DB operations.

Covers:
- save_store_report: creates Scan + StoreReport + channels + products + inventory
- dedup: same group_id+image_hash → is_duplicate=True, same id returned
- 5 queries: report_financials, revenue_by_channel, product_sales_report,
             inventory_latest, list_branches (branch=None vs specific branch)
- DATA_SCOPE: shared vs per_chat via monkeypatch
"""
from __future__ import annotations

from datetime import date

import pytest

import db.repository


# ── Seed helpers ──────────────────────────────────────────────────────────────

def _data(
    report_date: str = "2026-07-01",
    branch: str | None = "Cơ sở A",
    gross: int = 5_000_000,
    cost: int = 1_000_000,
    net: int = 4_000_000,
    cash: int = 3_000_000,
    transfer: int = 1_000_000,
    discrepancy: int = 0,
    channels: list | None = None,
    products: list | None = None,
    inventory: list | None = None,
) -> dict:
    if channels is None:
        channels = [
            {"channel": "cua_hang", "revenue": 3_000_000, "banh_qty": 10.0, "nuoc_qty": 5.0},
            {"channel": "grab", "revenue": 2_000_000, "banh_qty": 8.0, "nuoc_qty": 3.0},
        ]
    if products is None:
        products = [
            {
                "name": "Bánh mì",
                "category": "banh",
                "grab": 5.0,
                "now_shopee": 3.0,
                "xanh": 0.0,
                "be": 0.0,
                "cua_hang": 8.0,
                "total": 16.0,
            },
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
                "gross_revenue": gross,
                "cost": cost,
                "net_revenue": net,
                "cash": cash,
                "transfer": transfer,
                "discrepancy": discrepancy,
            },
            "channels": channels,
            "products": products,
            "inventory": inventory,
        },
    }


def _save(
    group_id: str = "g1",
    sender_id: str = "u1",
    sender_name: str = "Alice",
    image_url: str | None = "https://example.com/img.jpg",
    image_hash: str = "hash1",
    data: dict | None = None,
    branch_override: str | None = None,
):
    return db.repository.save_store_report(
        group_id=group_id,
        sender_id=sender_id,
        sender_name=sender_name,
        image_url=image_url,
        image_hash=image_hash,
        data=data if data is not None else _data(),
        branch_override=branch_override,
    )


# ════════════════════════════════════════════════════════════════════════════
# 1. save_store_report — entity creation
# ════════════════════════════════════════════════════════════════════════════

class TestSaveStoreReport:

    def test_returns_not_duplicate(self, fresh_db):
        result = _save()
        assert result.is_duplicate is False

    def test_document_has_id(self, fresh_db):
        result = _save()
        assert result.document.id is not None

    def test_document_branch_from_data(self, fresh_db):
        result = _save(data=_data(branch="Cơ sở B"))
        assert result.document.branch == "Cơ sở B"

    def test_branch_override_wins_over_image_branch(self, fresh_db):
        result = _save(data=_data(branch="Từ ảnh"), branch_override="Từ caption")
        assert result.document.branch == "Từ caption"

    def test_branch_override_none_uses_image_branch(self, fresh_db):
        result = _save(data=_data(branch="Từ ảnh"), branch_override=None)
        assert result.document.branch == "Từ ảnh"

    def test_gross_revenue_stored(self, fresh_db):
        result = _save(data=_data(gross=6_000_000))
        assert result.document.gross_revenue == 6_000_000

    def test_cost_stored(self, fresh_db):
        result = _save(data=_data(cost=1_200_000))
        assert result.document.cost == 1_200_000

    def test_net_revenue_stored(self, fresh_db):
        result = _save(data=_data(net=4_800_000))
        assert result.document.net_revenue == 4_800_000

    def test_channels_created(self, fresh_db):
        result = _save()
        assert len(result.document.channels) == 2

    def test_products_created(self, fresh_db):
        result = _save()
        assert len(result.document.products) == 1

    def test_inventory_created(self, fresh_db):
        result = _save()
        assert len(result.document.inventory) == 1

    def test_empty_channels_products_inventory(self, fresh_db):
        data = _data(channels=[], products=[], inventory=[])
        result = _save(data=data)
        assert len(result.document.channels) == 0
        assert len(result.document.products) == 0
        assert len(result.document.inventory) == 0

    def test_multiple_channels_all_saved(self, fresh_db):
        channels = [
            {"channel": "cua_hang", "revenue": 1_000_000, "banh_qty": 5.0, "nuoc_qty": 2.0},
            {"channel": "grab", "revenue": 2_000_000, "banh_qty": 4.0, "nuoc_qty": 1.0},
            {"channel": "now_shopee", "revenue": 500_000, "banh_qty": 2.0, "nuoc_qty": 1.0},
        ]
        result = _save(data=_data(channels=channels))
        assert len(result.document.channels) == 3

    def test_multiple_inventory_items(self, fresh_db):
        inventory = [
            {"name": "Bột mì", "open": 10.0, "import": 5.0, "discard": 0.5, "close": 14.5},
            {"name": "Đường", "open": 5.0, "import": 2.0, "discard": 0.0, "close": 7.0},
        ]
        result = _save(data=_data(inventory=inventory))
        assert len(result.document.inventory) == 2


# ════════════════════════════════════════════════════════════════════════════
# 2. Deduplication
# ════════════════════════════════════════════════════════════════════════════

class TestDedup:

    def test_same_hash_same_group_is_duplicate(self, fresh_db):
        r1 = _save(group_id="g1", image_hash="dupX")
        r2 = _save(group_id="g1", image_hash="dupX")
        assert r1.is_duplicate is False
        assert r2.is_duplicate is True

    def test_duplicate_returns_same_document_id(self, fresh_db):
        r1 = _save(group_id="g1", image_hash="dupY")
        r2 = _save(group_id="g1", image_hash="dupY")
        assert r2.document.id == r1.document.id

    def test_different_hash_not_duplicate(self, fresh_db):
        r1 = _save(group_id="g1", image_hash="h1")
        r2 = _save(group_id="g1", image_hash="h2")
        assert r1.is_duplicate is False
        assert r2.is_duplicate is False

    def test_same_hash_different_group_not_duplicate(self, fresh_db):
        r1 = _save(group_id="g1", image_hash="hX")
        r2 = _save(group_id="g2", image_hash="hX")
        assert r1.is_duplicate is False
        assert r2.is_duplicate is False
        assert r1.document.id != r2.document.id

    def test_third_save_same_hash_still_duplicate(self, fresh_db):
        _save(group_id="g1", image_hash="hZ")
        r2 = _save(group_id="g1", image_hash="hZ")
        r3 = _save(group_id="g1", image_hash="hZ")
        assert r2.is_duplicate is True
        assert r3.is_duplicate is True
        assert r2.document.id == r3.document.id


# ════════════════════════════════════════════════════════════════════════════
# 3. report_financials
# ════════════════════════════════════════════════════════════════════════════

class TestReportFinancials:

    def test_returns_correct_totals(self, fresh_db):
        _save(data=_data(
            report_date="2026-07-01",
            gross=5_000_000, cost=1_000_000, net=4_000_000,
            cash=3_000_000, transfer=1_000_000, discrepancy=50_000,
        ))
        result = db.repository.report_financials("g1", date(2026, 7, 1), date(2026, 7, 31))
        assert result["gross"] == 5_000_000
        assert result["cost"] == 1_000_000
        assert result["net"] == 4_000_000
        assert result["cash"] == 3_000_000
        assert result["transfer"] == 1_000_000
        assert result["discrepancy"] == 50_000
        assert result["count"] == 1

    def test_empty_db_returns_zeros(self, fresh_db):
        result = db.repository.report_financials("g1", date(2026, 7, 1), date(2026, 7, 31))
        assert result["gross"] == 0
        assert result["count"] == 0

    def test_sums_multiple_reports(self, fresh_db):
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-01", gross=3_000_000))
        _save(group_id="g1", image_hash="h2",
              data=_data(report_date="2026-07-02", gross=2_000_000))
        result = db.repository.report_financials("g1", date(2026, 7, 1), date(2026, 7, 31))
        assert result["gross"] == 5_000_000
        assert result["count"] == 2

    def test_branch_filter_includes_matching(self, fresh_db):
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-01", branch="Cơ sở A", gross=5_000_000))
        _save(group_id="g1", image_hash="h2",
              data=_data(report_date="2026-07-01", branch="Cơ sở B", gross=2_000_000))
        result = db.repository.report_financials(
            "g1", date(2026, 7, 1), date(2026, 7, 31), branch="Cơ sở A"
        )
        assert result["gross"] == 5_000_000
        assert result["count"] == 1

    def test_branch_filter_excludes_others(self, fresh_db):
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-01", branch="Cơ sở A"))
        result = db.repository.report_financials(
            "g1", date(2026, 7, 1), date(2026, 7, 31), branch="Cơ sở X"
        )
        assert result["gross"] == 0
        assert result["count"] == 0

    def test_branch_none_aggregates_all(self, fresh_db):
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-01", branch="Cơ sở A", gross=5_000_000))
        _save(group_id="g1", image_hash="h2",
              data=_data(report_date="2026-07-01", branch="Cơ sở B", gross=2_000_000))
        result = db.repository.report_financials("g1", date(2026, 7, 1), date(2026, 7, 31))
        assert result["gross"] == 7_000_000
        assert result["count"] == 2

    def test_date_range_excludes_out_of_range(self, fresh_db):
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-06-15", gross=1_000_000))
        result = db.repository.report_financials("g1", date(2026, 7, 1), date(2026, 7, 31))
        assert result["gross"] == 0


# ════════════════════════════════════════════════════════════════════════════
# 4. revenue_by_channel
# ════════════════════════════════════════════════════════════════════════════

class TestRevenueByChannel:

    def test_returns_channel_rows(self, fresh_db):
        channels = [
            {"channel": "cua_hang", "revenue": 3_000_000, "banh_qty": 10.0, "nuoc_qty": 5.0},
            {"channel": "grab", "revenue": 2_000_000, "banh_qty": 4.0, "nuoc_qty": 2.0},
        ]
        _save(data=_data(report_date="2026-07-01", channels=channels))
        rows = db.repository.revenue_by_channel("g1", date(2026, 7, 1), date(2026, 7, 31))
        assert len(rows) == 2
        names = {r["channel"] for r in rows}
        assert "cua_hang" in names
        assert "grab" in names

    def test_revenue_sorted_desc(self, fresh_db):
        channels = [
            {"channel": "grab", "revenue": 1_000_000, "banh_qty": 0.0, "nuoc_qty": 0.0},
            {"channel": "cua_hang", "revenue": 5_000_000, "banh_qty": 0.0, "nuoc_qty": 0.0},
        ]
        _save(data=_data(report_date="2026-07-01", channels=channels))
        rows = db.repository.revenue_by_channel("g1", date(2026, 7, 1), date(2026, 7, 31))
        assert rows[0]["channel"] == "cua_hang"
        assert rows[0]["revenue"] == 5_000_000

    def test_qty_fields_returned(self, fresh_db):
        channels = [
            {"channel": "grab", "revenue": 2_000_000, "banh_qty": 7.0, "nuoc_qty": 3.0},
        ]
        _save(data=_data(report_date="2026-07-01", channels=channels))
        rows = db.repository.revenue_by_channel("g1", date(2026, 7, 1), date(2026, 7, 31))
        assert rows[0]["banh_qty"] == 7.0
        assert rows[0]["nuoc_qty"] == 3.0

    def test_branch_filter(self, fresh_db):
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-01", branch="Cơ sở A",
                         channels=[{"channel": "grab", "revenue": 999_000,
                                    "banh_qty": 1.0, "nuoc_qty": 0.0}]))
        _save(group_id="g1", image_hash="h2",
              data=_data(report_date="2026-07-01", branch="Cơ sở B",
                         channels=[{"channel": "cua_hang", "revenue": 500_000,
                                    "banh_qty": 0.0, "nuoc_qty": 0.0}]))
        rows = db.repository.revenue_by_channel(
            "g1", date(2026, 7, 1), date(2026, 7, 31), branch="Cơ sở A"
        )
        assert len(rows) == 1
        assert rows[0]["channel"] == "grab"

    def test_branch_none_aggregates_channels(self, fresh_db):
        channels_a = [{"channel": "grab", "revenue": 1_000_000, "banh_qty": 2.0, "nuoc_qty": 0.0}]
        channels_b = [{"channel": "grab", "revenue": 500_000, "banh_qty": 1.0, "nuoc_qty": 0.0}]
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-01", branch="Cơ sở A", channels=channels_a))
        _save(group_id="g1", image_hash="h2",
              data=_data(report_date="2026-07-01", branch="Cơ sở B", channels=channels_b))
        rows = db.repository.revenue_by_channel("g1", date(2026, 7, 1), date(2026, 7, 31))
        grab_row = next(r for r in rows if r["channel"] == "grab")
        assert grab_row["revenue"] == 1_500_000

    def test_empty_returns_empty_list(self, fresh_db):
        rows = db.repository.revenue_by_channel("g1", date(2026, 7, 1), date(2026, 7, 31))
        assert rows == []


# ════════════════════════════════════════════════════════════════════════════
# 5. product_sales_report
# ════════════════════════════════════════════════════════════════════════════

class TestProductSalesReport:

    def test_returns_product_rows(self, fresh_db):
        prods = [
            {"name": "Bánh mì", "category": "banh",
             "grab": 5.0, "now_shopee": 3.0, "xanh": 0.0, "be": 0.0, "cua_hang": 8.0, "total": 16.0},
        ]
        _save(data=_data(report_date="2026-07-01", products=prods))
        rows = db.repository.product_sales_report("g1", date(2026, 7, 1), date(2026, 7, 31))
        assert len(rows) == 1
        assert rows[0]["name"] == "Bánh mì"
        assert rows[0]["total"] == 16.0
        assert rows[0]["category"] == "banh"

    def test_sorted_by_total_desc(self, fresh_db):
        prods = [
            {"name": "Ít bán", "category": "banh",
             "grab": 0.0, "now_shopee": 0.0, "xanh": 0.0, "be": 0.0, "cua_hang": 0.0, "total": 3.0},
            {"name": "Bán nhiều", "category": "nuoc",
             "grab": 0.0, "now_shopee": 0.0, "xanh": 0.0, "be": 0.0, "cua_hang": 0.0, "total": 20.0},
        ]
        _save(data=_data(report_date="2026-07-01", products=prods))
        rows = db.repository.product_sales_report("g1", date(2026, 7, 1), date(2026, 7, 31))
        assert rows[0]["name"] == "Bán nhiều"

    def test_name_like_filter(self, fresh_db):
        prods = [
            {"name": "Bánh mì", "category": "banh",
             "grab": 0.0, "now_shopee": 0.0, "xanh": 0.0, "be": 0.0, "cua_hang": 0.0, "total": 10.0},
            {"name": "Trà sữa", "category": "nuoc",
             "grab": 0.0, "now_shopee": 0.0, "xanh": 0.0, "be": 0.0, "cua_hang": 0.0, "total": 5.0},
        ]
        _save(data=_data(report_date="2026-07-01", products=prods))
        rows = db.repository.product_sales_report(
            "g1", date(2026, 7, 1), date(2026, 7, 31), name_like="Bánh"
        )
        assert len(rows) == 1
        assert rows[0]["name"] == "Bánh mì"

    def test_branch_filter(self, fresh_db):
        prods_a = [{"name": "SP A", "category": "banh",
                    "grab": 0.0, "now_shopee": 0.0, "xanh": 0.0, "be": 0.0, "cua_hang": 0.0, "total": 10.0}]
        prods_b = [{"name": "SP B", "category": "nuoc",
                    "grab": 0.0, "now_shopee": 0.0, "xanh": 0.0, "be": 0.0, "cua_hang": 0.0, "total": 5.0}]
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-01", branch="Cơ sở A", products=prods_a))
        _save(group_id="g1", image_hash="h2",
              data=_data(report_date="2026-07-01", branch="Cơ sở B", products=prods_b))
        rows = db.repository.product_sales_report(
            "g1", date(2026, 7, 1), date(2026, 7, 31), branch="Cơ sở A"
        )
        assert all(r["name"] == "SP A" for r in rows)

    def test_limit_applied(self, fresh_db):
        prods = [
            {"name": f"SP {i}", "category": "banh",
             "grab": 0.0, "now_shopee": 0.0, "xanh": 0.0, "be": 0.0, "cua_hang": 0.0,
             "total": float(10 - i)}
            for i in range(8)
        ]
        _save(data=_data(report_date="2026-07-01", products=prods))
        rows = db.repository.product_sales_report(
            "g1", date(2026, 7, 1), date(2026, 7, 31), limit=3
        )
        assert len(rows) <= 3

    def test_empty_returns_empty_list(self, fresh_db):
        rows = db.repository.product_sales_report("g1", date(2026, 7, 1), date(2026, 7, 31))
        assert rows == []


# ════════════════════════════════════════════════════════════════════════════
# 6. inventory_latest
# ════════════════════════════════════════════════════════════════════════════

class TestInventoryLatest:

    def test_returns_inventory_rows(self, fresh_db):
        inv = [
            {"name": "Bột mì", "open": 10.0, "import": 5.0, "discard": 0.5, "close": 14.5},
        ]
        _save(data=_data(report_date="2026-07-01", inventory=inv))
        rows = db.repository.inventory_latest("g1")
        assert len(rows) == 1
        assert rows[0]["name"] == "Bột mì"
        assert rows[0]["close"] == 14.5
        assert rows[0]["open"] == 10.0
        assert rows[0]["import"] == 5.0
        assert rows[0]["discard"] == 0.5

    def test_returns_date_field(self, fresh_db):
        inv = [{"name": "Bột mì", "open": 0.0, "import": 0.0, "discard": 0.0, "close": 5.0}]
        _save(data=_data(report_date="2026-07-10", inventory=inv))
        rows = db.repository.inventory_latest("g1")
        assert rows[0]["date"] == "2026-07-10"

    def test_latest_date_wins_over_older(self, fresh_db):
        inv_old = [{"name": "Bột mì", "open": 5.0, "import": 0.0, "discard": 0.0, "close": 5.0}]
        inv_new = [{"name": "Bột mì", "open": 5.0, "import": 3.0, "discard": 0.5, "close": 7.5}]
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-01", inventory=inv_old))
        _save(group_id="g1", image_hash="h2",
              data=_data(report_date="2026-07-10", inventory=inv_new))
        rows = db.repository.inventory_latest("g1")
        assert len(rows) == 1
        assert rows[0]["close"] == 7.5

    def test_branch_filter(self, fresh_db):
        inv_a = [{"name": "Hàng A", "open": 1.0, "import": 0.0, "discard": 0.0, "close": 1.0}]
        inv_b = [{"name": "Hàng B", "open": 2.0, "import": 0.0, "discard": 0.0, "close": 2.0}]
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-01", branch="Cơ sở A", inventory=inv_a))
        _save(group_id="g1", image_hash="h2",
              data=_data(report_date="2026-07-01", branch="Cơ sở B", inventory=inv_b))
        rows = db.repository.inventory_latest("g1", branch="Cơ sở A")
        assert len(rows) == 1
        assert rows[0]["name"] == "Hàng A"

    def test_name_like_filter(self, fresh_db):
        inv = [
            {"name": "Bột mì", "open": 10.0, "import": 0.0, "discard": 0.0, "close": 10.0},
            {"name": "Đường trắng", "open": 5.0, "import": 0.0, "discard": 0.0, "close": 5.0},
        ]
        _save(data=_data(report_date="2026-07-01", inventory=inv))
        rows = db.repository.inventory_latest("g1", name_like="Bột")
        assert len(rows) == 1
        assert rows[0]["name"] == "Bột mì"

    def test_returns_branch_field(self, fresh_db):
        """inventory_latest dict mỗi dòng phải có key 'branch'."""
        inv = [{"name": "Bột mì", "open": 10.0, "import": 0.0, "discard": 0.0, "close": 10.0}]
        _save(data=_data(report_date="2026-07-01", branch="Cơ sở A", inventory=inv))
        rows = db.repository.inventory_latest("g1")
        assert "branch" in rows[0]
        assert rows[0]["branch"] == "Cơ sở A"

    def test_two_branches_same_item_same_date_returns_two_rows(self, fresh_db):
        """F1: 2 cơ sở cùng ngày cùng item → 2 dòng, mỗi dòng branch khác nhau (không trùng vô danh)."""
        inv = [{"name": "Bột mì", "open": 5.0, "import": 0.0, "discard": 0.0, "close": 5.0}]
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-10", branch="Cơ sở 1", inventory=inv))
        inv2 = [{"name": "Bột mì", "open": 8.0, "import": 0.0, "discard": 0.0, "close": 8.0}]
        _save(group_id="g1", image_hash="h2",
              data=_data(report_date="2026-07-10", branch="Cơ sở 2", inventory=inv2))
        rows = db.repository.inventory_latest("g1")
        assert len(rows) == 2
        assert {r["branch"] for r in rows} == {"Cơ sở 1", "Cơ sở 2"}

    def test_two_branches_same_item_different_dates_each_gets_own_latest(self, fresh_db):
        """Mỗi cơ sở lấy ngày mới nhất của cơ sở đó, không bị ảnh hưởng lẫn nhau."""
        inv_a_old = [{"name": "Đường", "open": 3.0, "import": 0.0, "discard": 0.0, "close": 3.0}]
        inv_a_new = [{"name": "Đường", "open": 3.0, "import": 2.0, "discard": 0.0, "close": 5.0}]
        inv_b = [{"name": "Đường", "open": 10.0, "import": 0.0, "discard": 1.0, "close": 9.0}]
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-01", branch="Cơ sở A", inventory=inv_a_old))
        _save(group_id="g1", image_hash="h2",
              data=_data(report_date="2026-07-05", branch="Cơ sở A", inventory=inv_a_new))
        _save(group_id="g1", image_hash="h3",
              data=_data(report_date="2026-07-03", branch="Cơ sở B", inventory=inv_b))
        rows = db.repository.inventory_latest("g1")
        assert len(rows) == 2
        row_a = next(r for r in rows if r["branch"] == "Cơ sở A")
        row_b = next(r for r in rows if r["branch"] == "Cơ sở B")
        assert row_a["close"] == 5.0   # lấy 2026-07-05
        assert row_b["close"] == 9.0   # lấy 2026-07-03

    def test_empty_returns_empty_list(self, fresh_db):
        rows = db.repository.inventory_latest("g1")
        assert rows == []


# ════════════════════════════════════════════════════════════════════════════
# 7. list_branches
# ════════════════════════════════════════════════════════════════════════════

class TestListBranches:

    def test_returns_branch_list(self, fresh_db):
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-01", branch="Cơ sở A"))
        _save(group_id="g1", image_hash="h2",
              data=_data(report_date="2026-07-01", branch="Cơ sở B"))
        rows = db.repository.list_branches("g1")
        assert "Cơ sở A" in rows
        assert "Cơ sở B" in rows

    def test_sorted_alphabetically(self, fresh_db):
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-01", branch="Cơ sở Z"))
        _save(group_id="g1", image_hash="h2",
              data=_data(report_date="2026-07-01", branch="Cơ sở A"))
        _save(group_id="g1", image_hash="h3",
              data=_data(report_date="2026-07-01", branch="Cơ sở M"))
        rows = db.repository.list_branches("g1")
        assert rows == sorted(rows)

    def test_date_filter_start_end(self, fresh_db):
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-06-01", branch="Cũ"))
        _save(group_id="g1", image_hash="h2",
              data=_data(report_date="2026-07-01", branch="Mới"))
        rows = db.repository.list_branches("g1", start=date(2026, 7, 1), end=date(2026, 7, 31))
        assert "Mới" in rows
        assert "Cũ" not in rows

    def test_no_date_filter_returns_all(self, fresh_db):
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-06-01", branch="Tháng 6"))
        _save(group_id="g1", image_hash="h2",
              data=_data(report_date="2026-07-01", branch="Tháng 7"))
        rows = db.repository.list_branches("g1")
        assert "Tháng 6" in rows
        assert "Tháng 7" in rows

    def test_null_branch_excluded(self, fresh_db):
        _save(data=_data(branch=None))
        rows = db.repository.list_branches("g1")
        assert rows == []

    def test_empty_string_branch_excluded(self, fresh_db):
        _save(data=_data(branch=""))
        rows = db.repository.list_branches("g1")
        assert rows == []

    def test_no_duplicates_in_result(self, fresh_db):
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-01", branch="Cơ sở A"))
        _save(group_id="g1", image_hash="h2",
              data=_data(report_date="2026-07-02", branch="Cơ sở A"))
        rows = db.repository.list_branches("g1")
        assert rows.count("Cơ sở A") == 1

    def test_empty_returns_empty_list(self, fresh_db):
        rows = db.repository.list_branches("g1")
        assert rows == []


# ════════════════════════════════════════════════════════════════════════════
# 8. Branch filter — case-insensitive + strip (F2)
# ════════════════════════════════════════════════════════════════════════════

class TestBranchFilterCaseInsensitive:
    """F2: _branch_filter phải khớp case-insensitive + strip."""

    def test_lowercase_query_matches_titlecase_stored(self, fresh_db):
        """Query "cơ sở 2" phải khớp "Cơ sở 2" đã lưu."""
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-01", branch="Cơ sở 2", gross=5_000_000))
        result = db.repository.report_financials(
            "g1", date(2026, 7, 1), date(2026, 7, 31), branch="cơ sở 2"
        )
        assert result["gross"] == 5_000_000
        assert result["count"] == 1

    def test_uppercase_query_matches_lowercase_stored(self, fresh_db):
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-01", branch="cơ sở a", gross=3_000_000))
        result = db.repository.report_financials(
            "g1", date(2026, 7, 1), date(2026, 7, 31), branch="CƠ SỞ A"
        )
        assert result["gross"] == 3_000_000

    def test_query_with_surrounding_spaces_matches(self, fresh_db):
        """Strip khoảng trắng đầu/cuối trong branch query."""
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-01", branch="Cơ sở 1", gross=2_000_000))
        result = db.repository.report_financials(
            "g1", date(2026, 7, 1), date(2026, 7, 31), branch="  Cơ sở 1  "
        )
        assert result["gross"] == 2_000_000

    def test_inventory_latest_branch_case_insensitive(self, fresh_db):
        """inventory_latest cũng phải dùng case-insensitive filter qua _branch_filter."""
        inv = [{"name": "Bột mì", "open": 5.0, "import": 0.0, "discard": 0.0, "close": 5.0}]
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-01", branch="Cơ sở 2", inventory=inv))
        rows = db.repository.inventory_latest("g1", branch="cơ sở 2")
        assert len(rows) == 1
        assert rows[0]["name"] == "Bột mì"

    def test_wrong_branch_still_excluded(self, fresh_db):
        """Chỉ nới lỏng về chữ hoa/thường, không bỏ qua tên hoàn toàn khác."""
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-01", branch="Cơ sở A", gross=5_000_000))
        result = db.repository.report_financials(
            "g1", date(2026, 7, 1), date(2026, 7, 31), branch="Cơ sở B"
        )
        assert result["gross"] == 0
        assert result["count"] == 0


# ════════════════════════════════════════════════════════════════════════════
# 9. DATA_SCOPE: shared vs per_chat
# ════════════════════════════════════════════════════════════════════════════

class TestDataScope:

    def test_shared_scope_report_financials_sees_all_groups(self, fresh_db, monkeypatch):
        monkeypatch.setattr(db.repository, "_DATA_SCOPE", "shared")
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-01", gross=3_000_000))
        _save(group_id="g2", image_hash="h2",
              data=_data(report_date="2026-07-01", gross=2_000_000))
        # Query with g1 in shared mode — should see both groups' data
        result = db.repository.report_financials("g1", date(2026, 7, 1), date(2026, 7, 31))
        assert result["gross"] == 5_000_000

    def test_per_chat_scope_report_financials_filters_by_group(self, fresh_db, monkeypatch):
        monkeypatch.setattr(db.repository, "_DATA_SCOPE", "per_chat")
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-01", gross=3_000_000))
        _save(group_id="g2", image_hash="h2",
              data=_data(report_date="2026-07-01", gross=2_000_000))
        result = db.repository.report_financials("g1", date(2026, 7, 1), date(2026, 7, 31))
        assert result["gross"] == 3_000_000
        assert result["count"] == 1

    def test_per_chat_scope_list_branches_filtered(self, fresh_db, monkeypatch):
        monkeypatch.setattr(db.repository, "_DATA_SCOPE", "per_chat")
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-01", branch="Cơ sở G1"))
        _save(group_id="g2", image_hash="h2",
              data=_data(report_date="2026-07-01", branch="Cơ sở G2"))
        rows = db.repository.list_branches("g1")
        assert "Cơ sở G1" in rows
        assert "Cơ sở G2" not in rows

    def test_shared_scope_list_branches_sees_all(self, fresh_db, monkeypatch):
        monkeypatch.setattr(db.repository, "_DATA_SCOPE", "shared")
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-01", branch="Cơ sở G1"))
        _save(group_id="g2", image_hash="h2",
              data=_data(report_date="2026-07-01", branch="Cơ sở G2"))
        rows = db.repository.list_branches("g1")
        assert "Cơ sở G1" in rows
        assert "Cơ sở G2" in rows

    def test_per_chat_scope_inventory_filtered(self, fresh_db, monkeypatch):
        monkeypatch.setattr(db.repository, "_DATA_SCOPE", "per_chat")
        inv_g1 = [{"name": "Hàng G1", "open": 1.0, "import": 0.0, "discard": 0.0, "close": 1.0}]
        inv_g2 = [{"name": "Hàng G2", "open": 2.0, "import": 0.0, "discard": 0.0, "close": 2.0}]
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-01", inventory=inv_g1))
        _save(group_id="g2", image_hash="h2",
              data=_data(report_date="2026-07-01", inventory=inv_g2))
        rows = db.repository.inventory_latest("g1")
        assert all(r["name"] == "Hàng G1" for r in rows)

    def test_per_chat_scope_channels_filtered(self, fresh_db, monkeypatch):
        monkeypatch.setattr(db.repository, "_DATA_SCOPE", "per_chat")
        ch_g1 = [{"channel": "grab", "revenue": 3_000_000, "banh_qty": 5.0, "nuoc_qty": 2.0}]
        ch_g2 = [{"channel": "cua_hang", "revenue": 1_000_000, "banh_qty": 2.0, "nuoc_qty": 0.0}]
        _save(group_id="g1", image_hash="h1",
              data=_data(report_date="2026-07-01", channels=ch_g1))
        _save(group_id="g2", image_hash="h2",
              data=_data(report_date="2026-07-01", channels=ch_g2))
        rows = db.repository.revenue_by_channel("g1", date(2026, 7, 1), date(2026, 7, 31))
        assert len(rows) == 1
        assert rows[0]["channel"] == "grab"


# ════════════════════════════════════════════════════════════════════════════
# N. Gộp nhiều ảnh cùng lượt gửi (merge_report_id)
# ════════════════════════════════════════════════════════════════════════════

def _inv_only(report_date=None, branch=None, inventory=None, image_hash="hinv"):
    """Ảnh TỒN KHO đứng một mình: không kênh, không tổng tiền, không cơ sở."""
    if inventory is None:
        inventory = [{"name": "HA", "open": 100.0, "import": 0.0, "discard": 0.0, "close": 22.0}]
    return _data(
        report_date=report_date or "2026-07-20",  # ảnh tồn kho thường bị gán "hôm nay"
        branch=branch, gross=0, cost=0, net=0, cash=0, transfer=0,
        channels=[], products=[], inventory=inventory,
    )


class TestMergeStoreReport:
    def test_revenue_then_inventory_merges_same_report(self, fresh_db):
        rev = db.repository.save_store_report(
            group_id="g1", sender_id="u1", sender_name="A",
            image_url=None, image_hash="h_rev",
            data=_data(report_date="2026-07-19", branch="Trần Đăng Ninh", inventory=[]),
        )
        rid = rev.document.id
        merged = db.repository.save_store_report(
            group_id="g1", sender_id="u1", sender_name="A",
            image_url=None, image_hash="h_inv",
            data=_inv_only(), merge_report_id=rid,
        )
        assert merged.is_merged is True
        assert merged.is_duplicate is False
        assert merged.document.id == rid           # cùng 1 báo cáo
        # Ngày + cơ sở GIỮ NGUYÊN (ảnh tồn kho không phải sheet doanh thu)
        assert merged.document.report_date == date(2026, 7, 19)
        assert merged.document.branch == "Trần Đăng Ninh"
        # Có cả kênh (từ ảnh doanh thu) lẫn tồn kho (ảnh vừa ghép)
        assert len(merged.document.channels) == 2
        assert len(merged.document.inventory) == 1

    def test_inventory_first_then_revenue_backfills_date_branch(self, fresh_db):
        # Ảnh tồn kho gửi TRƯỚC: date=hôm nay, branch=None
        inv = db.repository.save_store_report(
            group_id="g1", sender_id="u1", sender_name="A",
            image_url=None, image_hash="h_inv",
            data=_inv_only(report_date="2026-07-20", branch=None),
        )
        rid = inv.document.id
        assert inv.document.branch is None
        # Ảnh doanh thu gửi SAU → ghép + backfill ngày/cơ sở/tổng tiền
        merged = db.repository.save_store_report(
            group_id="g1", sender_id="u1", sender_name="A",
            image_url=None, image_hash="h_rev",
            data=_data(report_date="2026-07-19", branch="Trần Đăng Ninh", inventory=[]),
            merge_report_id=rid,
        )
        assert merged.is_merged is True
        assert merged.document.id == rid
        assert merged.document.report_date == date(2026, 7, 19)   # backfilled
        assert merged.document.branch == "Trần Đăng Ninh"          # backfilled
        assert merged.document.net_revenue == 4_000_000            # totals ghi đè
        assert len(merged.document.inventory) == 1
        assert len(merged.document.channels) == 2

    def test_merged_inventory_visible_in_inventory_latest(self, fresh_db):
        rev = db.repository.save_store_report(
            group_id="g1", sender_id="u1", sender_name="A",
            image_url=None, image_hash="h_rev",
            data=_data(report_date="2026-07-19", branch="Trần Đăng Ninh", inventory=[]),
        )
        db.repository.save_store_report(
            group_id="g1", sender_id="u1", sender_name="A",
            image_url=None, image_hash="h_inv",
            data=_inv_only(inventory=[
                {"name": "HA", "open": 100.0, "import": 0.0, "discard": 0.0, "close": 22.0},
                {"name": "G", "open": 30.0, "import": 0.0, "discard": 0.0, "close": 15.0},
            ]),
            merge_report_id=rev.document.id,
        )
        rows = db.repository.inventory_latest("g1")
        names = {r["name"] for r in rows}
        assert "HA" in names and "G" in names

    def test_resending_merged_image_is_duplicate(self, fresh_db):
        rev = db.repository.save_store_report(
            group_id="g1", sender_id="u1", sender_name="A",
            image_url=None, image_hash="h_rev", data=_data(),
        )
        db.repository.save_store_report(
            group_id="g1", sender_id="u1", sender_name="A",
            image_url=None, image_hash="h_inv",
            data=_inv_only(), merge_report_id=rev.document.id,
        )
        # Gửi lại đúng ảnh tồn kho đó → phải nhận là trùng, không nhân đôi
        again = db.repository.save_store_report(
            group_id="g1", sender_id="u1", sender_name="A",
            image_url=None, image_hash="h_inv",
            data=_inv_only(), merge_report_id=rev.document.id,
        )
        assert again.is_duplicate is True
        rows = db.repository.inventory_latest("g1")
        # Chỉ 1 dòng HA (không nhân đôi do gửi lại)
        assert len([r for r in rows if r["name"] == "HA"]) == 1

    def test_no_merge_target_creates_new_report(self, fresh_db):
        r1 = db.repository.save_store_report(
            group_id="g1", sender_id="u1", sender_name="A",
            image_url=None, image_hash="h1", data=_data(),
        )
        r2 = db.repository.save_store_report(
            group_id="g1", sender_id="u1", sender_name="A",
            image_url=None, image_hash="h2", data=_data(),
            merge_report_id=None,
        )
        assert r2.is_merged is False
        assert r2.document.id != r1.document.id


# ════════════════════════════════════════════════════════════════════════════
# 10. delete_store_report — xoá báo cáo cửa hàng (+ cascade), tôn trọng DATA_SCOPE
# ════════════════════════════════════════════════════════════════════════════

class TestDeleteStoreReport:
    def test_delete_removes_report_and_children(self, fresh_db):
        r = _save(image_hash="hdel")
        rid = r.document.id
        ok = db.repository.delete_store_report(rid, "g1")
        assert ok is True
        # Không còn tồn kho của báo cáo đó
        assert db.repository.inventory_latest("g1") == []
        # Xoá lần 2 → False
        assert db.repository.delete_store_report(rid, "g1") is False

    def test_delete_missing_returns_false(self, fresh_db):
        assert db.repository.delete_store_report(999999, "g1") is False

    def test_shared_scope_deletes_across_chats(self, fresh_db, monkeypatch):
        monkeypatch.setattr(db.repository, "_DATA_SCOPE", "shared")
        r = _save(group_id="g1", image_hash="hs")
        # Xoá từ chat khác (g2) vẫn được vì kho chung
        assert db.repository.delete_store_report(r.document.id, "g2") is True

    def test_per_chat_scope_blocks_other_chat(self, fresh_db, monkeypatch):
        monkeypatch.setattr(db.repository, "_DATA_SCOPE", "per_chat")
        r = _save(group_id="g1", image_hash="hp")
        assert db.repository.delete_store_report(r.document.id, "g2") is False
        assert db.repository.delete_store_report(r.document.id, "g1") is True

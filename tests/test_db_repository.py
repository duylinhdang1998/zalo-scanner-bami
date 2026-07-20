"""Tests for db/repository.py — save, dedup, delete, aggregation queries.

All tests use the `fresh_db` fixture (isolated SQLite per test).
"""
from __future__ import annotations

from datetime import date

import pytest

from db import repository as repo
from db.models import Document, LineItem, Scan


# ── helpers ───────────────────────────────────────────────────────────────────

TODAY = date.today().isoformat()

_SALE_DATA = {
    "doc_type": "sale",
    "confidence": 0.90,
    "doc_date": TODAY,
    "party_name": "Khách A",
    "total_amount": 500_000,
    "currency": "VND",
    "status": None,
    "tracking_code": None,
    "note": None,
    "items": [
        {"product_name": "Áo thun", "sku": "AT01", "quantity": 2, "unit_price": 150_000, "amount": 300_000},
        {"product_name": "Nón",     "sku": "N01",  "quantity": 1, "unit_price": 200_000, "amount": 200_000},
    ],
}

_ORDER_DATA = {
    "doc_type": "order",
    "confidence": 0.85,
    "doc_date": TODAY,
    "party_name": "Người nhận B",
    "total_amount": 200_000,
    "currency": "VND",
    "status": "cho_giao",
    "tracking_code": "GHN123456",
    "note": None,
    "items": [
        {"product_name": "Nón", "sku": "N01", "quantity": 1, "unit_price": 200_000, "amount": 200_000},
    ],
}


# ── save_extraction / save_extraction_v2 ──────────────────────────────────────

class TestSaveExtraction:
    def test_creates_scan_document_lineitems(self, fresh_db):
        """save_extraction phải tạo Scan + Document + LineItem."""
        result = repo.save_extraction(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url=None, data=_SALE_DATA,
        )
        assert isinstance(result, Document)
        assert result.id is not None
        assert result.doc_type == "sale"
        assert result.total_amount == 500_000
        assert result.party_name == "Khách A"
        assert len(result.items) == 2

    def test_lineitems_saved_correctly(self, fresh_db):
        doc = repo.save_extraction(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url=None, data=_SALE_DATA,
        )
        names = {it.product_name for it in doc.items}
        assert "Áo thun" in names
        assert "Nón" in names

    def test_save_returns_saveresult_v2(self, fresh_db):
        """save_extraction_v2 trả SaveResult với is_duplicate=False khi lưu mới."""
        result = repo.save_extraction_v2(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url=None, data=_SALE_DATA,
        )
        assert isinstance(result, repo.SaveResult)
        assert result.is_duplicate is False
        assert isinstance(result.document, Document)

    def test_scan_fields_populated(self, fresh_db):
        """Scan phải có group_id, image_hash, doc_type, confidence."""
        repo.save_extraction(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url="https://img.zadn.vn/test.jpg",
            data=_SALE_DATA,
            image_hash="abc123",
        )
        Session = fresh_db
        with Session() as s:
            scan = s.query(Scan).first()
        assert scan.group_id == "g1"
        assert scan.image_hash == "abc123"
        assert scan.doc_type == "sale"
        assert scan.confidence == pytest.approx(0.90, abs=0.01)

    def test_no_items_saves_empty_lineitems(self, fresh_db):
        data = {**_SALE_DATA, "items": []}
        doc = repo.save_extraction(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url=None, data=data,
        )
        assert doc.items == []

    def test_document_group_id_set(self, fresh_db):
        doc = repo.save_extraction(
            group_id="grp99", sender_id="u1", sender_name="Linh",
            image_url=None, data=_SALE_DATA,
        )
        assert doc.group_id == "grp99"

    def test_created_by_uses_sender_name(self, fresh_db):
        doc = repo.save_extraction(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url=None, data=_SALE_DATA,
        )
        assert doc.created_by == "Linh"

    def test_created_by_falls_back_to_sender_id(self, fresh_db):
        doc = repo.save_extraction(
            group_id="g1", sender_id="u99", sender_name=None,
            image_url=None, data=_SALE_DATA,
        )
        assert doc.created_by == "u99"


# ── Dedup ─────────────────────────────────────────────────────────────────────

class TestDedup:
    def test_same_group_and_hash_is_duplicate(self, fresh_db):
        """Cùng group_id + image_hash → is_duplicate=True, không tạo mới."""
        repo.save_extraction_v2(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url=None, data=_SALE_DATA, image_hash="hash_abc",
        )
        result2 = repo.save_extraction_v2(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url=None, data=_SALE_DATA, image_hash="hash_abc",
        )
        assert result2.is_duplicate is True

    def test_duplicate_returns_existing_document(self, fresh_db):
        """Duplicate phải trả về Document đã có (cùng id)."""
        r1 = repo.save_extraction_v2(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url=None, data=_SALE_DATA, image_hash="hash_abc",
        )
        r2 = repo.save_extraction_v2(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url=None, data=_SALE_DATA, image_hash="hash_abc",
        )
        assert r2.document.id == r1.document.id

    def test_no_new_scan_created_on_dedup(self, fresh_db):
        """Sau khi dedup, DB chỉ có 1 Scan."""
        repo.save_extraction_v2(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url=None, data=_SALE_DATA, image_hash="hash_abc",
        )
        repo.save_extraction_v2(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url=None, data=_SALE_DATA, image_hash="hash_abc",
        )
        Session = fresh_db
        with Session() as s:
            count = s.query(Scan).count()
        assert count == 1

    def test_different_group_same_hash_not_duplicate(self, fresh_db):
        """Khác group_id, cùng hash → không phải duplicate."""
        repo.save_extraction_v2(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url=None, data=_SALE_DATA, image_hash="hash_abc",
        )
        result2 = repo.save_extraction_v2(
            group_id="g2", sender_id="u1", sender_name="Linh",
            image_url=None, data=_SALE_DATA, image_hash="hash_abc",
        )
        assert result2.is_duplicate is False

    def test_same_group_different_hash_not_duplicate(self, fresh_db):
        """Cùng group, khác hash → không phải duplicate."""
        repo.save_extraction_v2(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url=None, data=_SALE_DATA, image_hash="hash_abc",
        )
        result2 = repo.save_extraction_v2(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url=None, data=_SALE_DATA, image_hash="hash_xyz",
        )
        assert result2.is_duplicate is False

    def test_none_hash_never_dedup(self, fresh_db):
        """image_hash=None → không bao giờ dedup (NULL là distinct)."""
        r1 = repo.save_extraction_v2(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url=None, data=_SALE_DATA, image_hash=None,
        )
        r2 = repo.save_extraction_v2(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url=None, data=_SALE_DATA, image_hash=None,
        )
        assert r1.is_duplicate is False
        assert r2.is_duplicate is False
        assert r1.document.id != r2.document.id


# ── delete_document ────────────────────────────────────────────────────────────

class TestDeleteDocument:
    def test_delete_existing_document_same_group(self, fresh_db):
        doc = repo.save_extraction(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url=None, data=_SALE_DATA,
        )
        ok = repo.delete_document(doc.id, "g1")
        assert ok is True

    def test_delete_wrong_group_returns_false(self, fresh_db):
        """Xoá với group_id sai → False (scope kiểm tra)."""
        doc = repo.save_extraction(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url=None, data=_SALE_DATA,
        )
        ok = repo.delete_document(doc.id, "wrong_group")
        assert ok is False

    def test_delete_nonexistent_returns_false(self, fresh_db):
        ok = repo.delete_document(999999, "g1")
        assert ok is False

    def test_delete_removes_document_from_db(self, fresh_db):
        doc = repo.save_extraction(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url=None, data=_SALE_DATA,
        )
        doc_id = doc.id
        repo.delete_document(doc_id, "g1")
        Session = fresh_db
        with Session() as s:
            found = s.get(Document, doc_id)
        assert found is None

    def test_delete_with_none_group_id_deletes(self, fresh_db):
        """group_id=None → xoá không kiểm tra scope."""
        doc = repo.save_extraction(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url=None, data=_SALE_DATA,
        )
        ok = repo.delete_document(doc.id, None)
        assert ok is True


# ── Aggregation queries ────────────────────────────────────────────────────────

def _seed(fresh_db, group_id: str = "g1") -> None:
    """Seed dữ liệu chuẩn: 2 sale + 1 order trong cùng nhóm."""
    repo.save_extraction(
        group_id=group_id, sender_id="u1", sender_name="Linh",
        image_url=None,
        data={**_SALE_DATA, "total_amount": 450_000, "party_name": "KH A"},
    )
    repo.save_extraction(
        group_id=group_id, sender_id="u2", sender_name="Hà",
        image_url=None,
        data={**_SALE_DATA, "total_amount": 300_000, "party_name": "KH B"},
    )
    repo.save_extraction(
        group_id=group_id, sender_id="u1", sender_name="Linh",
        image_url=None, data=_ORDER_DATA,
    )


class TestRevenueSummary:
    def test_total_and_count(self, fresh_db):
        _seed(fresh_db)
        start = end = date.today()
        result = repo.revenue_summary("g1", start, end)
        assert result["total"] == pytest.approx(750_000, abs=1)
        assert result["count"] == 2  # chỉ sale

    def test_empty_group_returns_zero(self, fresh_db):
        result = repo.revenue_summary("no-such-group", date.today(), date.today())
        assert result["total"] == 0.0
        assert result["count"] == 0

    def test_group_isolation(self, fresh_db):
        """Nhóm khác không lẫn vào kết quả."""
        _seed(fresh_db, "g1")
        _seed(fresh_db, "g2")
        r1 = repo.revenue_summary("g1", date.today(), date.today())
        r2 = repo.revenue_summary("g2", date.today(), date.today())
        assert r1["total"] == pytest.approx(r2["total"])
        assert r1["count"] == r2["count"]


class TestRevenueBySellerQuery:
    def test_returns_sellers(self, fresh_db):
        _seed(fresh_db)
        rows = repo.revenue_by_seller("g1", date.today(), date.today())
        sellers = {r["seller"] for r in rows}
        assert "Linh" in sellers
        assert "Hà" in sellers

    def test_total_correct_per_seller(self, fresh_db):
        _seed(fresh_db)
        rows = repo.revenue_by_seller("g1", date.today(), date.today())
        by_seller = {r["seller"]: r["total"] for r in rows}
        assert by_seller["Linh"] == pytest.approx(450_000)
        assert by_seller["Hà"] == pytest.approx(300_000)

    def test_empty_returns_empty_list(self, fresh_db):
        rows = repo.revenue_by_seller("no-group", date.today(), date.today())
        assert rows == []


class TestTopProducts:
    def test_returns_products(self, fresh_db):
        _seed(fresh_db)
        rows = repo.top_products("g1", date.today(), date.today(), 5)
        names = {r["product"] for r in rows}
        assert "Áo thun" in names or "Nón" in names

    def test_limit_respected(self, fresh_db):
        _seed(fresh_db)
        rows = repo.top_products("g1", date.today(), date.today(), 1)
        assert len(rows) <= 1

    def test_empty_returns_empty_list(self, fresh_db):
        rows = repo.top_products("no-group", date.today(), date.today(), 5)
        assert rows == []


class TestOrdersByStatus:
    def test_returns_status_counts(self, fresh_db):
        _seed(fresh_db)
        rows = repo.orders_by_status("g1", date.today(), date.today())
        statuses = {r["status"] for r in rows}
        assert "cho_giao" in statuses

    def test_no_sales_in_orders(self, fresh_db):
        """revenue_summary chỉ đếm sale, orders_by_status chỉ đếm order."""
        _seed(fresh_db)
        rows = repo.orders_by_status("g1", date.today(), date.today())
        total_orders = sum(r["count"] for r in rows)
        assert total_orders == 1  # chỉ 1 order trong seed

    def test_empty_returns_empty_list(self, fresh_db):
        rows = repo.orders_by_status("no-group", date.today(), date.today())
        assert rows == []


# ── revenue_by_customer (Sprint 2) ────────────────────────────────────────────

class TestRevenueByCustomer:
    def test_returns_customers(self, fresh_db):
        """Phải có đủ 2 khách (KH A + KH B) từ dữ liệu seed."""
        _seed(fresh_db)
        rows = repo.revenue_by_customer("g1", date.today(), date.today())
        customers = {r["customer"] for r in rows}
        assert "KH A" in customers
        assert "KH B" in customers

    def test_total_correct_per_customer(self, fresh_db):
        """Tổng doanh thu mỗi khách phải khớp dữ liệu seed (KH A=450k, KH B=300k)."""
        _seed(fresh_db)
        rows = repo.revenue_by_customer("g1", date.today(), date.today())
        by_customer = {r["customer"]: r["total"] for r in rows}
        assert by_customer["KH A"] == pytest.approx(450_000)
        assert by_customer["KH B"] == pytest.approx(300_000)

    def test_sorted_desc_by_total(self, fresh_db):
        """Kết quả được sắp xếp giảm dần theo tổng doanh thu."""
        _seed(fresh_db)
        rows = repo.revenue_by_customer("g1", date.today(), date.today())
        totals = [r["total"] for r in rows]
        assert totals == sorted(totals, reverse=True)

    def test_none_party_name_becomes_khong_ro(self, fresh_db):
        """party_name = None (NULL trong DB) → hiển thị '(không rõ)'."""
        repo.save_extraction(
            group_id="g1", sender_id="u1", sender_name="Linh",
            image_url=None,
            data={**_SALE_DATA, "total_amount": 100_000, "party_name": None},
        )
        rows = repo.revenue_by_customer("g1", date.today(), date.today())
        names = {r["customer"] for r in rows}
        assert "(không rõ)" in names

    def test_limit_respected(self, fresh_db):
        """limit=1 → trả về tối đa 1 bản ghi."""
        _seed(fresh_db)
        rows = repo.revenue_by_customer("g1", date.today(), date.today(), limit=1)
        assert len(rows) <= 1

    def test_limit_returns_top_customer(self, fresh_db):
        """limit=1 → phải trả khách có doanh thu cao nhất (KH A=450k)."""
        _seed(fresh_db)
        rows = repo.revenue_by_customer("g1", date.today(), date.today(), limit=1)
        assert rows[0]["customer"] == "KH A"
        assert rows[0]["total"] == pytest.approx(450_000)

    def test_only_sale_doc_type_counted(self, fresh_db):
        """order không được tính vào doanh thu theo khách."""
        _seed(fresh_db)  # includes 1 order (_ORDER_DATA, Người nhận B)
        rows = repo.revenue_by_customer("g1", date.today(), date.today())
        customers = {r["customer"] for r in rows}
        assert "Người nhận B" not in customers  # order, không phải sale

    def test_empty_group_returns_empty_list(self, fresh_db):
        rows = repo.revenue_by_customer("no-such-group", date.today(), date.today())
        assert rows == []

    def test_group_isolation(self, fresh_db):
        """Nhóm g2 không lẫn vào g1."""
        _seed(fresh_db, "g1")
        _seed(fresh_db, "g2")
        r1 = repo.revenue_by_customer("g1", date.today(), date.today())
        r2 = repo.revenue_by_customer("g2", date.today(), date.today())
        assert len(r1) == len(r2)


# ── product_detail (Sprint 2) ─────────────────────────────────────────────────

class TestProductDetail:
    def test_returns_matching_product(self, fresh_db):
        """LIKE 'Áo thun' (exact case) → tổng hợp từ LineItem của sale documents.

        SQLite LIKE case-insensitive chỉ cho ASCII; tên có dấu như "Áo thun"
        cần khớp đúng case. Dùng "Áo thun" → LIKE '%Áo thun%' → match.
        """
        _seed(fresh_db)
        d = repo.product_detail("g1", date.today(), date.today(), "Áo thun")
        assert d["product"] == "Áo thun"
        assert d["qty"] > 0
        assert d["amount"] > 0

    def test_nonexistent_product_returns_zeros(self, fresh_db):
        """Tên sản phẩm không tồn tại → qty=0, amount=0, count=0."""
        _seed(fresh_db)
        d = repo.product_detail("g1", date.today(), date.today(), "xxx-không-có")
        assert d["qty"] == 0
        assert d["amount"] == 0
        assert d["count"] == 0

    def test_percent_wildcard_does_not_match_all(self, fresh_db):
        """CRITICAL: name_like='%' phải được escape — KHÔNG match tất cả sản phẩm.

        Nếu không escape, '%' sẽ trở thành LIKE '%%%' khớp tất cả LineItem
        → lộ toàn bộ dữ liệu (data enumeration vulnerability).
        """
        _seed(fresh_db)
        d = repo.product_detail("g1", date.today(), date.today(), "%")
        # Với escape đúng, '%' literal không khớp tên sản phẩm nào ("Áo thun", "Nón")
        assert d["qty"] == 0
        assert d["amount"] == 0
        assert d["count"] == 0

    def test_underscore_does_not_act_as_wildcard(self, fresh_db):
        """name_like='_' phải được escape — '_' là 1 ký tự LIKE wildcard.

        Không escape → '_' khớp mọi tên 1-ký-tự, có thể match các SKU ngắn.
        """
        _seed(fresh_db)
        d = repo.product_detail("g1", date.today(), date.today(), "_")
        assert d["count"] == 0

    def test_only_sale_doc_type_counted(self, fresh_db):
        """LineItem từ order document KHÔNG được tính."""
        _seed(fresh_db)  # _ORDER_DATA có 'Nón'
        # Tính riêng từ sale: "Nón" chỉ có trong _SALE_DATA (qty=1, amount=200_000)
        d_sale = repo.product_detail("g1", date.today(), date.today(), "Nón")
        # Nếu order cũng bị đếm thì qty=2, amount=400_000
        assert d_sale["count"] == pytest.approx(2)  # 2 sale docs đều có "Nón"

    def test_partial_match_like(self, fresh_db):
        """LIKE '%thun%' phải khớp 'Áo thun' (case-insensitive on SQLite)."""
        _seed(fresh_db)
        d = repo.product_detail("g1", date.today(), date.today(), "thun")
        assert d["count"] > 0

    def test_empty_group_returns_zeros(self, fresh_db):
        d = repo.product_detail("no-group", date.today(), date.today(), "áo thun")
        assert d["qty"] == 0
        assert d["count"] == 0


# ── list_recent (Sprint 2) ────────────────────────────────────────────────────

class TestListRecent:
    def test_returns_list_of_dicts(self, fresh_db):
        _seed(fresh_db)
        rows = repo.list_recent("g1")
        assert isinstance(rows, list)
        assert len(rows) > 0
        assert isinstance(rows[0], dict)

    def test_has_required_fields(self, fresh_db):
        """Mỗi row phải có id, doc_type, doc_date, party_name, total_amount, status."""
        _seed(fresh_db)
        rows = repo.list_recent("g1")
        required = {"id", "doc_type", "doc_date", "party_name", "total_amount", "status"}
        for row in rows:
            assert required.issubset(row.keys()), f"Thiếu fields: {required - row.keys()}"

    def test_doc_date_is_isoformat_or_none(self, fresh_db):
        """doc_date phải là string ISO hoặc None."""
        _seed(fresh_db)
        rows = repo.list_recent("g1")
        for row in rows:
            if row["doc_date"] is not None:
                # Phải parse được như date ISO (YYYY-MM-DD)
                assert len(row["doc_date"]) >= 10
                from datetime import date as _d
                _d.fromisoformat(row["doc_date"][:10])  # không raise → OK

    def test_limit_respected(self, fresh_db):
        """limit=1 → tối đa 1 bản ghi."""
        _seed(fresh_db)
        rows = repo.list_recent("g1", limit=1)
        assert len(rows) <= 1

    def test_default_limit_is_10(self, fresh_db):
        """Không truyền limit → tối đa 10 bản ghi."""
        # Seed nhiều hơn 10 docs
        for i in range(12):
            repo.save_extraction(
                group_id="g1", sender_id=f"u{i}", sender_name=f"User{i}",
                image_url=None,
                data={**_SALE_DATA, "total_amount": i * 10_000, "party_name": f"KH{i}"},
            )
        rows = repo.list_recent("g1")
        assert len(rows) <= 10

    def test_includes_both_sale_and_order(self, fresh_db):
        """list_recent không lọc theo doc_type — trả cả sale lẫn order."""
        _seed(fresh_db)
        rows = repo.list_recent("g1")
        doc_types = {r["doc_type"] for r in rows}
        assert "sale" in doc_types
        assert "order" in doc_types

    def test_empty_group_returns_empty_list(self, fresh_db):
        rows = repo.list_recent("no-group")
        assert rows == []

    def test_group_isolation(self, fresh_db):
        """Chỉ trả về doc của đúng group_id."""
        _seed(fresh_db, "g1")
        _seed(fresh_db, "g2")
        rows_g1 = repo.list_recent("g1")
        rows_g2 = repo.list_recent("g2")
        ids_g1 = {r["id"] for r in rows_g1}
        ids_g2 = {r["id"] for r in rows_g2}
        assert ids_g1.isdisjoint(ids_g2), "Hai nhóm không được dùng chung bản ghi"


# ── full_report (Sprint 2) ────────────────────────────────────────────────────

class TestFullReport:
    def test_returns_dict_with_all_keys(self, fresh_db):
        """full_report phải trả dict với đủ 5 keys: revenue, top_products, orders, by_seller, by_customer."""
        _seed(fresh_db)
        report = repo.full_report("g1", date.today(), date.today())
        assert isinstance(report, dict)
        expected_keys = {"revenue", "top_products", "orders", "by_seller", "by_customer"}
        assert expected_keys == report.keys()

    def test_revenue_key_has_total_and_count(self, fresh_db):
        """report['revenue'] = {'total': float, 'count': int}."""
        _seed(fresh_db)
        report = repo.full_report("g1", date.today(), date.today())
        rev = report["revenue"]
        assert "total" in rev
        assert "count" in rev
        assert rev["total"] == pytest.approx(750_000, abs=1)
        assert rev["count"] == 2

    def test_top_products_is_list(self, fresh_db):
        _seed(fresh_db)
        report = repo.full_report("g1", date.today(), date.today())
        assert isinstance(report["top_products"], list)

    def test_orders_is_list(self, fresh_db):
        _seed(fresh_db)
        report = repo.full_report("g1", date.today(), date.today())
        assert isinstance(report["orders"], list)

    def test_by_seller_is_list(self, fresh_db):
        _seed(fresh_db)
        report = repo.full_report("g1", date.today(), date.today())
        assert isinstance(report["by_seller"], list)

    def test_by_customer_is_list(self, fresh_db):
        _seed(fresh_db)
        report = repo.full_report("g1", date.today(), date.today())
        assert isinstance(report["by_customer"], list)

    def test_empty_group_returns_zeros(self, fresh_db):
        """Nhóm rỗng → revenue.total=0, revenue.count=0, lists rỗng."""
        report = repo.full_report("no-group", date.today(), date.today())
        assert report["revenue"]["total"] == 0
        assert report["revenue"]["count"] == 0
        assert report["top_products"] == []
        assert report["orders"] == []
        assert report["by_seller"] == []
        assert report["by_customer"] == []

    def test_composes_all_sub_queries(self, fresh_db):
        """Dữ liệu trong by_seller + by_customer nhất quán với revenue."""
        _seed(fresh_db)
        report = repo.full_report("g1", date.today(), date.today())
        # Tổng by_seller phải bằng revenue.total
        seller_total = sum(r["total"] for r in report["by_seller"])
        assert seller_total == pytest.approx(report["revenue"]["total"], abs=1)
        # Tổng by_customer cũng phải bằng revenue.total
        customer_total = sum(r["total"] for r in report["by_customer"])
        assert customer_total == pytest.approx(report["revenue"]["total"], abs=1)

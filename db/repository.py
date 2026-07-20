"""Lưu chứng từ + các truy vấn tổng hợp (thống kê).

DATA_SCOPE (đọc từ env tại đây, không qua config/settings.py):
  "shared"   — 1 kho chung; các query store_report BỎ lọc group_id.
  "per_chat" — lọc group_id như hành vi cũ sale/order.
Mặc định: "shared".
"""
from __future__ import annotations

import json
import os
from datetime import date
from typing import Any, NamedTuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from db.database import get_session
from db.models import Document, LineItem, ReportChannel, ReportInventory, ReportProduct, Scan, StoreReport

_DATA_SCOPE: str = os.getenv("DATA_SCOPE", "shared").strip().lower()


# ── Kiểu trả về cho save_extraction ───────────────────────────────
class SaveResult(NamedTuple):
    """Kết quả lưu ảnh.

    document  — Document đã lưu (hoặc đã có nếu trùng).
    is_duplicate — True nếu ảnh đã tồn tại (cùng group_id + image_hash).
    """
    document: Document
    is_duplicate: bool


class StoreReportSaveResult(NamedTuple):
    """Kết quả lưu báo cáo cửa hàng.

    document  — StoreReport đã lưu (hoặc đã có nếu trùng).
                Tên giữ là 'document' để không phá vỡ handlers đang dùng .document.id.
    is_duplicate — True nếu ảnh đã tồn tại (cùng group_id + image_hash).
    """
    document: StoreReport
    is_duplicate: bool


# ── Lưu ────────────────────────────────────────────────────────────
def save_extraction(
    *,
    group_id: str | None,
    sender_id: str | None,
    sender_name: str | None,
    image_url: str | None,
    data: dict[str, Any],
    image_hash: str | None = None,
) -> Document:
    """Ghi 1 Scan + 1 Document + các LineItem từ JSON model trả về.

    Tham số mới (optional — tương thích ngược):
        image_hash: sha256 hex của bytes ảnh gốc (64 chars).
            Nếu cung cấp và đã tồn tại bản ghi cùng group_id + image_hash,
            hàm trả về Document cũ mà KHÔNG tạo bản ghi mới.
            Caller muốn biết trùng hay không → dùng save_extraction_v2().

    Returns:
        Document đã tạo hoặc Document cũ (nếu trùng ảnh).
    """
    result = save_extraction_v2(
        group_id=group_id,
        sender_id=sender_id,
        sender_name=sender_name,
        image_url=image_url,
        data=data,
        image_hash=image_hash,
    )
    return result.document


def save_extraction_v2(
    *,
    group_id: str | None,
    sender_id: str | None,
    sender_name: str | None,
    image_url: str | None,
    data: dict[str, Any],
    image_hash: str | None = None,
) -> SaveResult:
    """Như save_extraction nhưng trả về SaveResult(document, is_duplicate).

    Dùng khi caller cần biết ảnh có bị trùng hay không để hiển thị thông báo.
    """
    # Kiểm tra trùng ảnh khi có hash (optimistic path — tránh round-trip DB không cần thiết)
    if image_hash and group_id:
        existing = _find_existing_by_hash(group_id, image_hash)
        if existing is not None:
            return SaveResult(document=existing, is_duplicate=True)

    try:
        with get_session() as s:
            scan = Scan(
                group_id=group_id,
                sender_id=sender_id,
                sender_name=sender_name,
                image_url=image_url,
                image_hash=image_hash,
                raw_json=json.dumps(data, ensure_ascii=False),
                doc_type=str(data.get("doc_type") or "unknown"),
                confidence=_safe_float(data.get("confidence"), 0.0),
                status="confirmed",
            )
            s.add(scan)
            s.flush()

            doc = Document(
                scan_id=scan.id,
                group_id=group_id,
                doc_type=str(data.get("doc_type") or "unknown"),
                doc_date=_parse_date(data.get("doc_date")),
                party_name=_safe_str(data.get("party_name")),
                total_amount=_safe_float(data.get("total_amount"), 0.0),
                currency=str(data.get("currency") or "VND"),
                status=_safe_str(data.get("status")),
                tracking_code=_safe_str(data.get("tracking_code")),
                note=_safe_str(data.get("note")),
                created_by=_safe_str(sender_name or sender_id),
            )
            s.add(doc)
            s.flush()

            for it in data.get("items") or []:
                s.add(
                    LineItem(
                        document_id=doc.id,
                        product_name=_safe_str(it.get("product_name")),
                        sku=_safe_str(it.get("sku")),
                        quantity=_safe_float(it.get("quantity"), 0.0),
                        unit_price=_safe_float(it.get("unit_price"), 0.0),
                        amount=_safe_float(it.get("amount"), 0.0),
                    )
                )
            s.flush()
            # Nạp sẵn quan hệ khi còn gắn session (tránh DetachedInstanceError)
            _ = len(doc.items)
            return SaveResult(document=doc, is_duplicate=False)

    except IntegrityError:
        # Race condition: 2 request đồng thời vượt qua optimistic check,
        # unique constraint (group_id, image_hash) bắt lần thứ 2.
        # Session đã được rollback bởi get_session() context manager.
        if image_hash and group_id:
            existing = _find_existing_by_hash(group_id, image_hash)
            if existing is not None:
                return SaveResult(document=existing, is_duplicate=True)
        raise


def _find_existing_by_hash(group_id: str, image_hash: str) -> Document | None:
    """Tìm Document đã lưu có cùng group_id + image_hash. Trả None nếu chưa có."""
    with get_session() as s:
        stmt = (
            select(Document)
            .join(Scan, Scan.id == Document.scan_id)
            .where(
                Scan.group_id == group_id,
                Scan.image_hash == image_hash,
            )
            .limit(1)
        )
        row = s.execute(stmt).scalar_one_or_none()
        if row is not None:
            # Nạp items để tránh DetachedInstanceError sau khi session đóng
            _ = len(row.items)
        return row


def delete_document(doc_id: int, group_id: str | None) -> bool:
    with get_session() as s:
        doc = s.get(Document, doc_id)
        if not doc or (group_id and doc.group_id != group_id):
            return False
        s.delete(doc)
        return True


# ── Thống kê ───────────────────────────────────────────────────────
def revenue_summary(group_id: str | None, start: date, end: date) -> dict[str, Any]:
    """Tổng doanh thu + số hoá đơn trong khoảng [start, end]."""
    with get_session() as s:
        q = select(
            func.coalesce(func.sum(Document.total_amount), 0.0),
            func.count(Document.id),
        ).where(
            Document.doc_type == "sale",
            Document.doc_date >= start,
            Document.doc_date <= end,
        )
        q = _scope(q, group_id)
        total, count = s.execute(q).one()
        return {"total": _safe_float(total, 0.0), "count": int(count or 0)}


def revenue_by_seller(group_id: str | None, start: date, end: date) -> list[dict]:
    with get_session() as s:
        q = (
            select(
                Document.created_by,
                func.coalesce(func.sum(Document.total_amount), 0.0),
                func.count(Document.id),
            )
            .where(
                Document.doc_type == "sale",
                Document.doc_date >= start,
                Document.doc_date <= end,
            )
            .group_by(Document.created_by)
            .order_by(func.sum(Document.total_amount).desc())
        )
        q = _scope(q, group_id)
        return [
            {"seller": r[0] or "(không rõ)", "total": _safe_float(r[1], 0.0), "count": int(r[2] or 0)}
            for r in s.execute(q).all()
        ]


def top_products(group_id: str | None, start: date, end: date, limit: int = 5) -> list[dict]:
    with get_session() as s:
        q = (
            select(
                LineItem.product_name,
                func.coalesce(func.sum(LineItem.quantity), 0.0),
                func.coalesce(func.sum(LineItem.amount), 0.0),
            )
            .join(Document, Document.id == LineItem.document_id)
            .where(
                # Chỉ tính từ hoá đơn bán (doc_type="sale") — nhất quán với revenue_summary.
                # Không lọc → sản phẩm có cả sale lẫn order bị đếm gấp đôi.
                Document.doc_type == "sale",
                Document.doc_date >= start,
                Document.doc_date <= end,
            )
            .group_by(LineItem.product_name)
            .order_by(func.sum(LineItem.quantity).desc())
            .limit(limit)
        )
        q = _scope(q, group_id)
        return [
            {"product": r[0] or "(không rõ)", "qty": _safe_float(r[1], 0.0), "amount": _safe_float(r[2], 0.0)}
            for r in s.execute(q).all()
        ]


def orders_by_status(group_id: str | None, start: date, end: date) -> list[dict]:
    with get_session() as s:
        q = (
            select(Document.status, func.count(Document.id))
            .where(
                Document.doc_type == "order",
                Document.doc_date >= start,
                Document.doc_date <= end,
            )
            .group_by(Document.status)
            .order_by(func.count(Document.id).desc())
        )
        q = _scope(q, group_id)
        return [
            {"status": r[0] or "(chưa rõ)", "count": int(r[1] or 0)} for r in s.execute(q).all()
        ]


def revenue_by_customer(
    group_id: str | None, start: date, end: date, limit: int = 5
) -> list[dict]:
    """Tổng doanh thu + số hoá đơn theo khách hàng (doc_type='sale') trong [start, end].

    Returns:
        list of {"customer": str, "total": float, "count": int} — sort desc by total.
        party_name None → "(không rõ)".
    """
    with get_session() as s:
        q = (
            select(
                Document.party_name,
                func.coalesce(func.sum(Document.total_amount), 0.0),
                func.count(Document.id),
            )
            .where(
                Document.doc_type == "sale",
                Document.doc_date >= start,
                Document.doc_date <= end,
            )
            .group_by(Document.party_name)
            .order_by(func.sum(Document.total_amount).desc())
            .limit(limit)
        )
        q = _scope(q, group_id)
        return [
            {
                "customer": r[0] or "(không rõ)",
                "total": _safe_float(r[1], 0.0),
                "count": int(r[2] or 0),
            }
            for r in s.execute(q).all()
        ]


def product_detail(
    group_id: str | None, start: date, end: date, name_like: str
) -> dict:
    """Chi tiết sản phẩm khớp LIKE %name_like% trong [start, end].

    Dùng parameterised LIKE — KHÔNG nối chuỗi SQL.

    Returns:
        {"product": name_like, "qty": float, "amount": float, "count": int}
        Không khớp → qty/amount/count = 0.
    """
    # Escape LIKE metachar ("%" "_") từ user-input trước khi nhúng vào pattern.
    # Không escape → name_like="%" match toàn bộ sản phẩm (data enumeration).
    escaped = name_like.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    with get_session() as s:
        q = (
            select(
                func.coalesce(func.sum(LineItem.quantity), 0.0),
                func.coalesce(func.sum(LineItem.amount), 0.0),
                func.count(LineItem.id),
            )
            .join(Document, Document.id == LineItem.document_id)
            .where(
                # Chỉ tính từ hoá đơn bán (doc_type="sale") — nhất quán với top_products.
                Document.doc_type == "sale",
                Document.doc_date >= start,
                Document.doc_date <= end,
                LineItem.product_name.like(f"%{escaped}%", escape="\\"),
            )
        )
        q = _scope(q, group_id)
        row = s.execute(q).one()
        return {
            "product": name_like,
            "qty": _safe_float(row[0], 0.0),
            "amount": _safe_float(row[1], 0.0),
            "count": int(row[2] or 0),
        }


def list_recent(group_id: str | None, limit: int = 10) -> list[dict]:
    """Documents mới nhất theo created_at desc.

    Returns:
        list of {"id", "doc_type", "doc_date" (iso str|None), "party_name",
                 "total_amount", "status"}
    """
    with get_session() as s:
        q = (
            select(
                Document.id,
                Document.doc_type,
                Document.doc_date,
                Document.party_name,
                Document.total_amount,
                Document.status,
            )
            .order_by(Document.created_at.desc())
            .limit(limit)
        )
        q = _scope(q, group_id)
        return [
            {
                "id": r[0],
                "doc_type": r[1],
                "doc_date": r[2].isoformat() if r[2] else None,
                "party_name": r[3],
                "total_amount": _safe_float(r[4], 0.0),
                "status": r[5],
            }
            for r in s.execute(q).all()
        ]


def full_report(group_id: str | None, start: date, end: date) -> dict:
    """Báo cáo tổng hợp: revenue, top_products, orders, by_seller, by_customer."""
    return {
        "revenue": revenue_summary(group_id, start, end),
        "top_products": top_products(group_id, start, end, 5),
        "orders": orders_by_status(group_id, start, end),
        "by_seller": revenue_by_seller(group_id, start, end),
        "by_customer": revenue_by_customer(group_id, start, end, 5),
    }


# ── Sprint 3: Báo cáo cửa hàng theo ngày ─────────────────────────────────────

def save_store_report(
    *,
    group_id: str | None,
    sender_id: str | None,
    sender_name: str | None,
    image_url: str | None,
    image_hash: str | None,
    data: dict[str, Any],
    branch_override: str | None = None,
) -> StoreReportSaveResult:
    """Ghi 1 Scan + 1 StoreReport + kênh/SP/tồn từ JSON model trả về.

    Dedup: cùng group_id + image_hash → trả StoreReport cũ (is_duplicate=True).
    branch_override: nếu không None, ghi đè branch đọc từ ảnh (dùng khi bot
                     lấy branch từ caption DM).

    Returns:
        StoreReportSaveResult(document=store_report, is_duplicate=bool)
        .document là StoreReport (field giữ tên 'document' để tương thích handlers).
    """
    # Optimistic dedup check
    if image_hash and group_id:
        existing = _find_store_report_by_hash(group_id, image_hash)
        if existing is not None:
            return StoreReportSaveResult(document=existing, is_duplicate=True)

    report_data: dict[str, Any] = data.get("report") or {}
    totals: dict[str, Any] = report_data.get("totals") or {}

    branch = branch_override if branch_override is not None else _safe_str(report_data.get("branch"))

    try:
        with get_session() as s:
            scan = Scan(
                group_id=group_id,
                sender_id=sender_id,
                sender_name=sender_name,
                image_url=image_url,
                image_hash=image_hash,
                raw_json=json.dumps(data, ensure_ascii=False),
                doc_type="store_report",
                confidence=_safe_float(data.get("confidence"), 0.0),
                status="confirmed",
            )
            s.add(scan)
            s.flush()

            rpt = StoreReport(
                scan_id=scan.id,
                group_id=group_id,
                report_date=_parse_date(report_data.get("report_date")),
                branch=branch,
                image_hash=image_hash,
                gross_revenue=int(_safe_float(totals.get("gross_revenue"), 0.0)),
                cost=int(_safe_float(totals.get("cost"), 0.0)),
                net_revenue=int(_safe_float(totals.get("net_revenue"), 0.0)),
                cash=int(_safe_float(totals.get("cash"), 0.0)),
                transfer=int(_safe_float(totals.get("transfer"), 0.0)),
                discrepancy=int(_safe_float(totals.get("discrepancy"), 0.0)),
                created_by=_safe_str(sender_name or sender_id),
            )
            s.add(rpt)
            s.flush()

            for ch in report_data.get("channels") or []:
                s.add(
                    ReportChannel(
                        report_id=rpt.id,
                        channel=str(ch.get("channel") or ""),
                        revenue=int(_safe_float(ch.get("revenue"), 0.0)),
                        banh_qty=_safe_float(ch.get("banh_qty"), 0.0),
                        nuoc_qty=_safe_float(ch.get("nuoc_qty"), 0.0),
                    )
                )

            for pr in report_data.get("products") or []:
                s.add(
                    ReportProduct(
                        report_id=rpt.id,
                        name=str(pr.get("name") or ""),
                        category=_safe_str(pr.get("category")),
                        qty_grab=_safe_float(pr.get("grab"), 0.0),
                        qty_now_shopee=_safe_float(pr.get("now_shopee"), 0.0),
                        qty_xanh=_safe_float(pr.get("xanh"), 0.0),
                        qty_be=_safe_float(pr.get("be"), 0.0),
                        qty_cua_hang=_safe_float(pr.get("cua_hang"), 0.0),
                        qty_total=_safe_float(pr.get("total"), 0.0),
                    )
                )

            for inv in report_data.get("inventory") or []:
                s.add(
                    ReportInventory(
                        report_id=rpt.id,
                        name=str(inv.get("name") or ""),
                        open_qty=_safe_float(inv.get("open"), 0.0),
                        import_qty=_safe_float(inv.get("import"), 0.0),
                        discard_qty=_safe_float(inv.get("discard"), 0.0),
                        close_qty=_safe_float(inv.get("close"), 0.0),
                    )
                )

            s.flush()
            # Nạp sẵn quan hệ trước khi session đóng
            _ = len(rpt.channels)
            _ = len(rpt.products)
            _ = len(rpt.inventory)
            return StoreReportSaveResult(document=rpt, is_duplicate=False)

    except IntegrityError:
        # Race condition — unique constraint bắt lần thứ 2
        if image_hash and group_id:
            existing = _find_store_report_by_hash(group_id, image_hash)
            if existing is not None:
                return StoreReportSaveResult(document=existing, is_duplicate=True)
        raise


def _find_store_report_by_hash(group_id: str, image_hash: str) -> StoreReport | None:
    """Tìm StoreReport đã có cùng group_id + image_hash."""
    with get_session() as s:
        row = s.execute(
            select(StoreReport).where(
                StoreReport.group_id == group_id,
                StoreReport.image_hash == image_hash,
            ).limit(1)
        ).scalar_one_or_none()
        if row is not None:
            _ = len(row.channels)
            _ = len(row.products)
            _ = len(row.inventory)
        return row


def _report_scope(q, group_id: str | None):
    """Áp DATA_SCOPE cho query StoreReport theo cấu hình env."""
    if _DATA_SCOPE == "per_chat" and group_id:
        return q.where(StoreReport.group_id == group_id)
    return q


def _branch_filter(q, branch: str | None):
    """Lọc theo branch — case-insensitive + strip để "cơ sở 2" khớp "Cơ sở 2".
    Bỏ dấu tiếng Việt là hạn chế đã biết, không xử lý ở đây.
    """
    if branch:
        b = branch.strip().lower()
        return q.where(func.lower(func.trim(StoreReport.branch)) == b)
    return q


def report_financials(
    group_id: str | None,
    start: date,
    end: date,
    branch: str | None = None,
) -> dict[str, Any]:
    """Tổng tài chính store_report trong [start, end].

    branch=None → gộp tất cả cơ sở.
    Tôn trọng DATA_SCOPE qua _report_scope().

    Returns:
        {"gross": int, "cost": int, "net": int, "cash": int,
         "transfer": int, "discrepancy": int, "count": int}
    """
    with get_session() as s:
        q = select(
            func.coalesce(func.sum(StoreReport.gross_revenue), 0),
            func.coalesce(func.sum(StoreReport.cost), 0),
            func.coalesce(func.sum(StoreReport.net_revenue), 0),
            func.coalesce(func.sum(StoreReport.cash), 0),
            func.coalesce(func.sum(StoreReport.transfer), 0),
            func.coalesce(func.sum(StoreReport.discrepancy), 0),
            func.count(StoreReport.id),
        ).where(
            StoreReport.report_date >= start,
            StoreReport.report_date <= end,
        )
        q = _report_scope(q, group_id)
        q = _branch_filter(q, branch)
        row = s.execute(q).one()
        return {
            "gross": int(row[0] or 0),
            "cost": int(row[1] or 0),
            "net": int(row[2] or 0),
            "cash": int(row[3] or 0),
            "transfer": int(row[4] or 0),
            "discrepancy": int(row[5] or 0),
            "count": int(row[6] or 0),
        }


def revenue_by_channel(
    group_id: str | None,
    start: date,
    end: date,
    branch: str | None = None,
) -> list[dict[str, Any]]:
    """Tổng doanh thu + số lượng theo kênh bán trong [start, end].

    branch=None → gộp tất cả cơ sở.

    Returns:
        list[{"channel": str, "revenue": int, "banh_qty": float, "nuoc_qty": float}]
        Sort theo revenue desc.
    """
    with get_session() as s:
        q = (
            select(
                ReportChannel.channel,
                func.coalesce(func.sum(ReportChannel.revenue), 0),
                func.coalesce(func.sum(ReportChannel.banh_qty), 0.0),
                func.coalesce(func.sum(ReportChannel.nuoc_qty), 0.0),
            )
            .join(StoreReport, StoreReport.id == ReportChannel.report_id)
            .where(
                StoreReport.report_date >= start,
                StoreReport.report_date <= end,
            )
            .group_by(ReportChannel.channel)
            .order_by(func.sum(ReportChannel.revenue).desc())
        )
        q = _report_scope(q, group_id)
        q = _branch_filter(q, branch)
        return [
            {
                "channel": r[0] or "",
                "revenue": int(r[1] or 0),
                "banh_qty": _safe_float(r[2], 0.0),
                "nuoc_qty": _safe_float(r[3], 0.0),
            }
            for r in s.execute(q).all()
        ]


def product_sales_report(
    group_id: str | None,
    start: date,
    end: date,
    branch: str | None = None,
    name_like: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Tổng số lượng bán theo sản phẩm trong [start, end].

    branch=None → gộp tất cả cơ sở.
    name_like: lọc LIKE %name_like% (None = không lọc).

    Returns:
        list[{"name": str, "category": str|None, "total": float}]
        Sort theo total desc, giới hạn limit bản ghi.
    """
    with get_session() as s:
        q = (
            select(
                ReportProduct.name,
                ReportProduct.category,
                func.coalesce(func.sum(ReportProduct.qty_total), 0.0),
            )
            .join(StoreReport, StoreReport.id == ReportProduct.report_id)
            .where(
                StoreReport.report_date >= start,
                StoreReport.report_date <= end,
            )
            .group_by(ReportProduct.name, ReportProduct.category)
            .order_by(func.sum(ReportProduct.qty_total).desc())
            .limit(limit)
        )
        q = _report_scope(q, group_id)
        q = _branch_filter(q, branch)
        if name_like:
            escaped = name_like.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            q = q.where(ReportProduct.name.like(f"%{escaped}%", escape="\\"))
        return [
            {"name": r[0] or "", "category": r[1], "total": _safe_float(r[2], 0.0)}
            for r in s.execute(q).all()
        ]


def inventory_latest(
    group_id: str | None,
    branch: str | None = None,
    name_like: str | None = None,
) -> list[dict[str, Any]]:
    """Tồn kho mới nhất cho mỗi (sản phẩm, cơ sở) — bản ghi có report_date lớn nhất
    của CƠ SỞ ĐÓ, trả 1 dòng mỗi (name, branch).

    branch=None → lấy tất cả cơ sở (mỗi cơ sở × mỗi tên = 1 dòng).
    branch="X"  → chỉ cơ sở X (case-insensitive + strip).

    Returns:
        list[{"name": str, "open": float, "import": float,
              "discard": float, "close": float,
              "date": str|None, "branch": str|None}]
        Mỗi (name, branch) xuất hiện đúng 1 lần — không trùng.
    """
    with get_session() as s:
        # Subquery: report_date mới nhất mỗi (name, branch).
        # Group by cả branch để tránh double-row khi 2 cơ sở cùng max_date cho 1 item.
        sub_q = (
            select(
                ReportInventory.name,
                StoreReport.branch,
                func.max(StoreReport.report_date).label("max_date"),
            )
            .join(StoreReport, StoreReport.id == ReportInventory.report_id)
        )
        sub_q = _report_scope(sub_q, group_id)
        sub_q = _branch_filter(sub_q, branch)
        if name_like:
            escaped = name_like.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            sub_q = sub_q.where(ReportInventory.name.like(f"%{escaped}%", escape="\\"))
        sub_q = sub_q.group_by(ReportInventory.name, StoreReport.branch).subquery()

        # Main query: join phải khớp cả (name, branch, date) — dùng coalesce để
        # xử lý NULL branch an toàn (NULL = NULL trong SQL là False nếu dùng ==).
        q = (
            select(
                ReportInventory.name,
                ReportInventory.open_qty,
                ReportInventory.import_qty,
                ReportInventory.discard_qty,
                ReportInventory.close_qty,
                StoreReport.report_date,
                StoreReport.branch,
            )
            .join(StoreReport, StoreReport.id == ReportInventory.report_id)
            .join(
                sub_q,
                (ReportInventory.name == sub_q.c.name)
                & (func.coalesce(StoreReport.branch, "") == func.coalesce(sub_q.c.branch, ""))
                & (StoreReport.report_date == sub_q.c.max_date),
            )
            .order_by(ReportInventory.name, StoreReport.branch)
        )
        q = _report_scope(q, group_id)
        q = _branch_filter(q, branch)
        return [
            {
                "name": r[0] or "",
                "open": _safe_float(r[1], 0.0),
                "import": _safe_float(r[2], 0.0),
                "discard": _safe_float(r[3], 0.0),
                "close": _safe_float(r[4], 0.0),
                "date": r[5].isoformat() if r[5] else None,
                "branch": r[6],
            }
            for r in s.execute(q).all()
        ]


def list_branches(
    group_id: str | None,
    start: date | None = None,
    end: date | None = None,
) -> list[str]:
    """Danh sách cơ sở (branch) đã có báo cáo trong khoảng [start, end].

    start/end=None → không lọc ngày.
    Tôn trọng DATA_SCOPE.

    Returns:
        list[str] — sort alphabetically, loại bỏ None/rỗng.
    """
    with get_session() as s:
        q = (
            select(StoreReport.branch)
            .where(StoreReport.branch.isnot(None), StoreReport.branch != "")
            .distinct()
            .order_by(StoreReport.branch)
        )
        if start:
            q = q.where(StoreReport.report_date >= start)
        if end:
            q = q.where(StoreReport.report_date <= end)
        q = _report_scope(q, group_id)
        return [r[0] for r in s.execute(q).all() if r[0]]


# ── Helpers ────────────────────────────────────────────────────────
def _scope(q, group_id: str | None):
    return q.where(Document.group_id == group_id) if group_id else q


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Ép kiểu float an toàn — trả default nếu None hoặc không ép được."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_str(value: Any) -> str | None:
    """Trả None nếu value là None/rỗng, ngược lại trả str."""
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None

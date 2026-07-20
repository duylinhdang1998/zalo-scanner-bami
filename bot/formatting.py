"""Định dạng số & câu trả lời tiếng Việt gửi lên Zalo."""
from __future__ import annotations


def vnd(amount: float) -> str:
    return f"{int(round(amount)):,}".replace(",", ".") + "đ"


def qty(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else f"{n:g}"


def revenue_block(label: str, summary: dict) -> str:
    return (
        f"📊 Doanh thu {label}\n"
        f"• Tổng: {vnd(summary.get('total', 0))}\n"
        f"• Số hoá đơn: {summary.get('count', 0)}"
    )


def sellers_block(label: str, rows: list[dict]) -> str:
    if not rows:
        return f"Chưa có dữ liệu bán hàng {label}."
    lines = [f"👥 Doanh thu theo người ({label})"]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. {r['seller']}: {vnd(r['total'])} ({r['count']} hoá đơn)")
    return "\n".join(lines)


def top_products_block(label: str, rows: list[dict]) -> str:
    if not rows:
        return f"Chưa có dữ liệu sản phẩm {label}."
    lines = [f"🏆 Top sản phẩm ({label})"]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. {r['product']}: {qty(r['qty'])} sp — {vnd(r['amount'])}")
    return "\n".join(lines)


def orders_block(label: str, rows: list[dict]) -> str:
    if not rows:
        return f"Chưa có đơn hàng {label}."
    names = {
        "cho_giao": "Chờ giao",
        "dang_giao": "Đang giao",
        "da_giao": "Đã giao",
        "huy": "Huỷ",
    }
    lines = [f"📦 Đơn hàng ({label})"]
    total = 0
    for r in rows:
        total += r["count"]
        lines.append(f"• {names.get(r['status'], r['status'])}: {r['count']}")
    lines.append(f"➡️ Tổng: {total} đơn")
    return "\n".join(lines)


def saved_block(doc_type: str, doc) -> str:
    kind = "hoá đơn" if doc_type == "sale" else "đơn hàng"
    parts = [f"✅ Đã lưu {kind} #{doc.id}"]
    if doc.party_name:
        parts.append(f"• KH: {doc.party_name}")
    if doc.total_amount:
        parts.append(f"• Tổng: {vnd(doc.total_amount)}")
    if doc.items:
        parts.append(f"• {len(doc.items)} sản phẩm")
    if doc.tracking_code:
        parts.append(f"• Mã: {doc.tracking_code}")
    parts.append("↩️ Sai? Gõ: /xoa " + str(doc.id))
    return "\n".join(parts)


# ── Các formatter mới (Sprint 2) ─────────────────────────────────────────────

def no_data(label: str) -> str:
    """Thông báo trung thực khi không có dữ liệu — không suy đoán."""
    return (
        f"Chưa có dữ liệu {label}. "
        "Mình chỉ trả lời từ số liệu đã lưu, không suy đoán."
    )


def customers_block(label: str, rows: list[dict]) -> str:
    """Top khách hàng theo doanh thu (tương tự sellers_block)."""
    if not rows:
        return no_data(f"khách hàng {label}")
    lines = [f"👤 Top khách hàng ({label})"]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. {r['customer']}: {vnd(r['total'])} ({r['count']} đơn)")
    return "\n".join(lines)


def product_detail_block(label: str, d: dict) -> str:
    """Chi tiết 1 sản phẩm {product, qty, amount, count}."""
    name = d.get("product") or "(không rõ)"
    lines = [f"📦 Sản phẩm: {name} ({label})"]
    if d.get("qty") is not None:
        lines.append(f"• Số lượng: {qty(d['qty'])} sp")
    if d.get("amount") is not None:
        lines.append(f"• Doanh thu: {vnd(d['amount'])}")
    if d.get("count") is not None:
        lines.append(f"• Số đơn: {d['count']}")
    return "\n".join(lines)


def recent_block(rows: list[dict]) -> str:
    """Danh sách chứng từ gần đây {id, doc_type, party_name, total_amount, doc_date}."""
    if not rows:
        return no_data("chứng từ gần đây")
    lines = ["📋 Chứng từ gần đây"]
    for r in rows:
        doc_id = r.get("id", "?")
        doc_type_tag = "HĐ" if r.get("doc_type") == "sale" else "ĐH"
        party = r.get("party_name") or "(không rõ)"
        amount_str = f" — {vnd(r['total_amount'])}" if r.get("total_amount") else ""
        date_str = str(r.get("doc_date") or "")[:10]
        date_tag = f" ({date_str})" if date_str else ""
        lines.append(f"• #{doc_id} [{doc_type_tag}] {party}{amount_str}{date_tag}")
    return "\n".join(lines)


# ── Các formatter Sprint 3 (store_report) ────────────────────────────────────

def store_report_saved_block(report_data: dict, doc, branch: str | None = None) -> str:
    """Tóm tắt báo cáo cửa hàng vừa lưu.

    report_data: dict từ data["report"] (JSON extractor).
    doc: đối tượng StoreReport có trường .id.
    branch: tên cơ sở đã xác định (caption hoặc từ ảnh).
    """
    report_date = report_data.get("report_date") or "?"
    branch_label = branch or report_data.get("branch") or "(không rõ)"
    totals = report_data.get("totals") or {}
    gross = totals.get("gross_revenue", 0) or 0
    net = totals.get("net_revenue", 0) or 0
    cash = totals.get("cash", 0) or 0
    transfer = totals.get("transfer", 0) or 0
    channels = report_data.get("channels") or []
    products = report_data.get("products") or []
    inventory = report_data.get("inventory") or []

    doc_id = getattr(doc, "id", "?") if doc is not None else "?"
    lines = [f"✅ Đã lưu báo cáo #{doc_id} — {branch_label} ({report_date})"]
    lines.append(f"• Doanh thu: {vnd(gross)} | Net: {vnd(net)}")
    lines.append(f"• Tiền mặt: {vnd(cash)} | Chuyển khoản: {vnd(transfer)}")
    if channels:
        lines.append(f"• {len(channels)} kênh bán")
    if products:
        lines.append(f"• {len(products)} sản phẩm")
    if inventory:
        lines.append(f"• {len(inventory)} mặt hàng tồn kho")
    return "\n".join(lines)


def store_report_merged_block(report_data: dict, doc) -> str:
    """Thông báo phần vừa GHÉP thêm vào báo cáo đang mở (ảnh cùng lượt gửi).

    report_data: dict của ẢNH VỪA GỬI (phần được thêm).
    doc: StoreReport sau khi ghép (đã cập nhật ngày/cơ sở) — dùng .id/.branch/.report_date.
    """
    channels = report_data.get("channels") or []
    products = report_data.get("products") or []
    inventory = report_data.get("inventory") or []
    added: list[str] = []
    if channels:
        added.append(f"{len(channels)} kênh doanh thu")
    if products:
        added.append(f"{len(products)} sản phẩm bán")
    if inventory:
        added.append(f"{len(inventory)} mặt hàng tồn kho")
    what = ", ".join(added) if added else "dữ liệu"

    doc_id = getattr(doc, "id", "?") if doc is not None else "?"
    branch_label = getattr(doc, "branch", None) or "(không rõ)"
    rdate = getattr(doc, "report_date", None)
    rdate_s = rdate.strftime("%d/%m/%Y") if rdate else "?"
    return (
        f"➕ Đã bổ sung {what} vào báo cáo #{doc_id} — {branch_label} ({rdate_s}).\n"
        f"(gộp các ảnh cùng lượt gửi vào 1 báo cáo)"
    )


def channels_block(label: str, rows: list[dict], branch: str | None = None) -> str:
    """Doanh thu theo kênh bán hàng.

    rows: list[{channel, revenue, banh_qty, nuoc_qty}]
    """
    if not rows:
        return no_data(f"kênh bán {label}")
    branch_tag = f" — {branch}" if branch else ""
    lines = [f"📡 Doanh thu theo kênh ({label}){branch_tag}"]
    _channel_names = {
        "cua_hang": "Cửa hàng",
        "grab": "Grab",
        "now_shopee": "Now/Shopee",
        "xanh": "Xanh SM",
        "be": "Be",
    }
    total = 0
    for r in rows:
        name = _channel_names.get(r.get("channel", ""), r.get("channel", "?"))
        rev = r.get("revenue", 0) or 0
        total += rev
        banh = r.get("banh_qty") or 0
        nuoc = r.get("nuoc_qty") or 0
        detail = ""
        if banh or nuoc:
            detail = f" (bánh:{qty(banh)} nước:{qty(nuoc)})"
        lines.append(f"• {name}: {vnd(rev)}{detail}")
    lines.append(f"➡️ Tổng: {vnd(total)}")
    return "\n".join(lines)


def financials_block(label: str, data: dict, branch: str | None = None) -> str:
    """Net / chi phí / tiền mặt / chuyển khoản.

    data: {gross, cost, net, cash, transfer, discrepancy, count}
    """
    branch_tag = f" — {branch}" if branch else ""
    lines = [f"💰 Tài chính ({label}){branch_tag}"]
    lines.append(f"• Doanh thu: {vnd(data.get('gross', 0) or 0)}")
    lines.append(f"• Chi phí: {vnd(data.get('cost', 0) or 0)}")
    lines.append(f"• Net: {vnd(data.get('net', 0) or 0)}")
    lines.append(f"• Tiền mặt: {vnd(data.get('cash', 0) or 0)}")
    lines.append(f"• Chuyển khoản: {vnd(data.get('transfer', 0) or 0)}")
    disc = data.get("discrepancy", 0) or 0
    if disc:
        lines.append(f"• Chênh lệch: {vnd(disc)}")
    count = data.get("count", 0) or 0
    if count:
        lines.append(f"• Số ngày: {count}")
    return "\n".join(lines)


def product_sales_block(label: str, rows: list[dict], branch: str | None = None) -> str:
    """Doanh số sản phẩm từ báo cáo cửa hàng.

    rows: list[{name, category, total}]
    """
    if not rows:
        return no_data(f"sản phẩm {label}")
    branch_tag = f" — {branch}" if branch else ""
    lines = [f"🥐 Sản phẩm ({label}){branch_tag}"]
    _cat_names = {"banh": "Bánh", "topping": "Topping", "nuoc": "Nước"}
    for i, r in enumerate(rows, 1):
        cat = _cat_names.get(r.get("category", ""), "")
        cat_tag = f" [{cat}]" if cat else ""
        lines.append(f"{i}. {r.get('name', '?')}{cat_tag}: {qty(r.get('total', 0))} sp")
    return "\n".join(lines)


def inventory_block(rows: list[dict], branch: str | None = None) -> str:
    """Tồn kho theo mặt hàng.

    rows: list[{name, open, import, discard, close, date[, branch]}]
    branch: nếu đã lọc 1 cơ sở → hiện trong header, không lặp mỗi dòng.
            Nếu None và rows có field 'branch' → hiện branch trên từng dòng.
    """
    if not rows:
        return no_data("tồn kho")
    branch_tag = f" — {branch}" if branch else ""
    date_tag = ""
    if rows and rows[0].get("date"):
        date_tag = f" ({rows[0]['date']})"
    lines = [f"📦 Tồn kho{branch_tag}{date_tag}"]
    show_branch_per_row = branch is None and any(r.get("branch") for r in rows)
    for r in rows:
        name = r.get("name", "?")
        close = r.get("close", 0) or 0
        discard = r.get("discard", 0) or 0
        discard_tag = f" (huỷ:{qty(discard)})" if discard else ""
        name_tag = f"{name} ({r['branch']})" if show_branch_per_row and r.get("branch") else name
        lines.append(f"• {name_tag}: tồn {qty(close)}{discard_tag}")
    return "\n".join(lines)


def branches_block(rows: list[str]) -> str:
    """Danh sách cơ sở/chi nhánh."""
    if not rows:
        return no_data("cơ sở")
    lines = [f"🏪 Danh sách cơ sở ({len(rows)})"]
    for i, b in enumerate(rows, 1):
        lines.append(f"{i}. {b}")
    return "\n".join(lines)


def report_block(label: str, report: dict) -> str:
    """Format dict từ repo.full_report thành báo cáo tổng hợp tiếng Việt.

    report keys: revenue, top_products, orders, by_seller, by_customer.
    Bỏ qua phần nào rỗng / None.
    """
    parts = [f"📊 Báo cáo {label}"]

    # Doanh thu tổng quan
    rev = report.get("revenue") or {}
    if rev:
        parts.append(
            f"• Doanh thu: {vnd(rev.get('total', 0))} / {rev.get('count', 0)} hoá đơn"
        )

    for key, fn in [
        ("top_products", top_products_block),
        ("orders", orders_block),
        ("by_seller", sellers_block),
        ("by_customer", customers_block),
    ]:
        rows = report.get(key) or []
        if rows:
            parts.append("")
            parts.append(fn(label, rows))

    return "\n".join(parts)

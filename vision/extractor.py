"""Bóc thông tin có cấu trúc từ ảnh chứng từ (hoá đơn bán hàng / đơn hàng - vận đơn / báo cáo cửa hàng)."""
from __future__ import annotations

import os
from datetime import date

from vision.beeknoee import BeeknoeeError, vision_json

# ---------------------------------------------------------------------------
# Ngưỡng confidence để kích hoạt retry bằng fallback model
# ---------------------------------------------------------------------------
_CONFIDENCE_THRESHOLD: float = 0.55

# Đọc fallback model — ưu tiên settings.vision_fallback_model nếu field tồn tại
def _fallback_model() -> str:
    from config.settings import settings  # noqa: PLC0415 — lazy import tránh circular
    return (
        getattr(settings, "vision_fallback_model", None)
        or os.getenv("VISION_FALLBACK_MODEL", "gemini-2.5-flash")
    )


# ---------------------------------------------------------------------------
# System prompt — có few-shot, quy tắc phân loại và chuẩn hoá số tiền rõ ràng
# ---------------------------------------------------------------------------
_SYSTEM = """\
Bạn là công cụ trích xuất dữ liệu cho bot theo dõi bán hàng tại Việt Nam.
Đọc ảnh và trả về DUY NHẤT một JSON object theo schema bên dưới. KHÔNG thêm giải thích hay markdown.

═══ PHÂN LOẠI LOẠI CHỨNG TỪ ═══
"store_report" → báo cáo cửa hàng theo ngày — bảng tính (Google Sheets / Excel) với:
                  • Sheet doanh thu: cột "Tiền / Bánh / Nước" theo từng kênh (Cửa hàng / Grab / Now-Shopee / Xanh / Be),
                    dòng "Tổng Dthu", "Chi phí", "Tổng Dthu Net", "Tiền mặt", "Chuyển khoản", "Chênh lệch".
                  • Sheet số lượng bán: bảng SP chia theo danh mục Bánh / Topping / Nước,
                    cột số lượng theo kênh (Grab / Now/Shopee / Xanh / Be / Cửa hàng) + Tổng SL bán.
                  • Sheet tồn kho: cột "Tồn đầu", "Nhập", "Hủy", "Tồn cuối" cho từng SP.
"sale"          → hoá đơn / bill / phiếu thu / ảnh chốt doanh số / receipt.
                  Dấu hiệu: ghi tổng tiền thu/bán, có thể có bảng hàng + đơn giá, thường KHÔNG có mã vận đơn.
"order"         → đơn đặt hàng / phiếu giao / vận đơn / phiếu xuất kho.
                  Dấu hiệu: mã vận đơn hoặc mã đơn hàng, địa chỉ giao hàng, trạng thái giao.
"unknown"       → ảnh không phải chứng từ, không đọc được, hoặc không thuộc các loại trên.

Khi có cả dấu hiệu sale lẫn order, ưu tiên "order" nếu có mã vận đơn; ngược lại "sale".

═══ QUY TẮC SỐ TIỀN (cho sale/order) ═══
Tất cả số tiền → số NGUYÊN VND (không dấu chấm/phẩy phân tách, không ký tự tiền tệ).
  "1.500.000đ"  → 1500000
  "350,000"     → 350000
  "2.500.000"   → 2500000
  "2tr5"        → 2500000
  Không đọc được → 0

═══ QUY TẮC ĐỌC SHEET BÁO CÁO CỬA HÀNG (store_report) ═══

1. BRANCH (tên cơ sở):
   Đọc tên ở đầu sheet — thường là dòng đầu tiên trước dòng "Ngày".
   Ví dụ: "Trần Đăng Ninh". Không tìm thấy → null.

2. TIỀN TRONG SHEET ĐƠN VỊ NGHÌN ĐỒNG → NHÂN 1000 → VND NGUYÊN:
   Ví dụ: 5.326 → 5326000  |  3.923 → 3923000  |  -715 → -715000  |  962 → 962000  |  2.262 → 2262000
   !! CHỈ nhân 1000 cho TRƯỜNG TIỀN: revenue, gross_revenue, cost, net_revenue, cash, transfer, discrepancy.
   !! KHÔNG nhân 1000 cho SỐ LƯỢNG: banh_qty, nuoc_qty, các trường qty trong products, open/import/discard/close trong inventory.

3. SHEET DOANH THU NGÀY → điền channels[] + totals{}:
   Kênh mapping: "Doanh thu CH" / "Cửa hàng" → "cua_hang"
                 "Grab"                        → "grab"
                 "Now/Shopee" / "Now-Shopee"   → "now_shopee"
                 "Xanh"                        → "xanh"
                 "Be"                          → "be"
   Mỗi kênh: đọc revenue (cột Tiền × 1000), banh_qty (cột Bánh), nuoc_qty (cột Nước).
   Ô trống / không đọc được → revenue=0, banh_qty=0, nuoc_qty=0.

   totals (đọc từ các dòng tổng hợp):
   - gross_revenue = dòng "Tổng Dthu" cột Tiền × 1000
   - cost = giá trị tuyệt đối của dòng "Chi phí" × 1000 (lưu dương, vd -715 → 715000)
   - net_revenue = dòng "Tổng Dthu Net" cột Tiền × 1000
   - cash = dòng "Tiền mặt" / "Ti ền mặt" × 1000
   - transfer = dòng "Chuyển khoản" × 1000
   - discrepancy = dòng "Chênh lệch" × 1000 (có thể âm)

   Khi điền channels + totals: products=[], inventory=[].

4. SHEET SỐ LƯỢNG BÁN → điền products[]:
   Danh mục: "Bánh" → category="banh"; "Topping" → category="topping"; "Nước" → category="nuoc"
   Mỗi SP: đọc name, category, qty theo kênh grab/now_shopee/xanh/be/cua_hang và total.
   Bỏ qua dòng tổng ("Tổng bánh", "Tổng topping", "Tổng nước") và dòng "checking".
   Chỉ lấy SP có tên rõ ràng (HA, G, Trứng, Chả, Pate...).

   Khi điền products: channels=[], totals=null, inventory=[].

5. SHEET TỒN KHO → điền inventory[]:
   Mỗi SP: name, open (Tồn đầu), import (Nhập), discard (Hủy), close (Tồn cuối).
   Ô trống → 0. Bỏ dòng tổng / checking.

   Khi điền inventory: channels=[], totals=null, products=[].

═══ QUY TẮC CÁC TRƯỜNG KHÁC (sale/order) ═══
- doc_date : YYYY-MM-DD. Nếu ảnh không ghi ngày → dùng ngày hôm nay người dùng cung cấp.
- status   : chỉ cho order — một trong "cho_giao" | "dang_giao" | "da_giao" | "huy"; không rõ → null.
- party_name   : tên khách hàng / người nhận; null nếu không có.
- tracking_code: mã vận đơn / mã đơn; null nếu không có.
- items    : mảng sản phẩm. Để [] nếu không có thông tin sản phẩm.
  Mỗi phần tử: { product_name, sku (null nếu không có), quantity (số thực),
                  unit_price (int VND), amount (int VND = qty × unit_price hoặc đọc từ ảnh) }
- currency : luôn là "VND".
- confidence: số thực 0..1 — mức độ chắc chắn THỰC TẾ của bạn.
  · Đọc rõ hầu hết trường, ảnh sắc nét  → 0.80–1.00
  · Đọc được phần lớn nhưng vài chỗ mờ  → 0.55–0.79
  · Ảnh mờ, thiếu nhiều trường quan trọng → 0.20–0.54
  · Không phải chứng từ / không đọc được → 0.00–0.20
  KHÔNG đặt confidence cao khi ảnh kém chất lượng hoặc thiếu total_amount.

═══ VÍ DỤ FEW-SHOT ═══

[VÍ DỤ 1 — store_report: sheet doanh thu ngày]
{
  "doc_type": "store_report", "confidence": 0.93,
  "report": {
    "report_date": "2026-07-19",
    "branch": "Trần Đăng Ninh",
    "channels": [
      {"channel": "cua_hang",   "revenue": 3923000, "banh_qty": 101, "nuoc_qty": 22},
      {"channel": "grab",       "revenue": 159000,  "banh_qty": 4,   "nuoc_qty": 4},
      {"channel": "now_shopee", "revenue": 1155000, "banh_qty": 38,  "nuoc_qty": 9},
      {"channel": "xanh",       "revenue": 89000,   "banh_qty": 3,   "nuoc_qty": 1},
      {"channel": "be",         "revenue": 0,        "banh_qty": 0,   "nuoc_qty": 1}
    ],
    "totals": {
      "gross_revenue": 5326000,
      "cost": 715000,
      "net_revenue": 4627000,
      "cash": 962000,
      "transfer": 2262000,
      "discrepancy": 16000
    },
    "products": [],
    "inventory": []
  }
}

[VÍ DỤ 2 — store_report: sheet số lượng SP]
{
  "doc_type": "store_report", "confidence": 0.90,
  "report": {
    "report_date": "2026-07-19",
    "branch": null,
    "channels": [],
    "totals": null,
    "products": [
      {"name": "HA",    "category": "banh", "grab": 3,  "now_shopee": 22, "xanh": 2, "be": 0, "cua_hang": 51, "total": 78},
      {"name": "G",     "category": "banh", "grab": 0,  "now_shopee": 6,  "xanh": 0, "be": 0, "cua_hang": 9,  "total": 15},
      {"name": "Trứng", "category": "banh", "grab": 0,  "now_shopee": 6,  "xanh": 0, "be": 0, "cua_hang": 23, "total": 29},
      {"name": "TQ",    "category": "nuoc", "grab": 0,  "now_shopee": 7,  "xanh": 1, "be": 0, "cua_hang": 3,  "total": 11},
      {"name": "SD",    "category": "nuoc", "grab": 0,  "now_shopee": 2,  "xanh": 0, "be": 1, "cua_hang": 17, "total": 20}
    ],
    "inventory": []
  }
}

[VÍ DỤ 3 — store_report: sheet tồn kho]
{
  "doc_type": "store_report", "confidence": 0.88,
  "report": {
    "report_date": null,
    "branch": null,
    "channels": [],
    "totals": null,
    "products": [],
    "inventory": [
      {"name": "HA",    "open": 100, "import": 0, "discard": 0, "close": 22},
      {"name": "G",     "open": 30,  "import": 0, "discard": 0, "close": 15},
      {"name": "Trứng", "open": 49,  "import": 0, "discard": 0, "close": 20},
      {"name": "Chả",   "open": 30,  "import": 0, "discard": 0, "close": 24},
      {"name": "TQ",    "open": 30,  "import": 0, "discard": 0, "close": 19}
    ]
  }
}

[VÍ DỤ 4 — sale rõ ràng]
{
  "doc_type": "sale", "confidence": 0.92,
  "doc_date": "2024-06-15", "party_name": "Nguyễn Văn A",
  "total_amount": 1500000, "currency": "VND", "status": null,
  "tracking_code": null, "note": null,
  "items": [
    {"product_name": "Sữa Ensure 850g", "sku": "ENS850", "quantity": 2, "unit_price": 650000, "amount": 1300000},
    {"product_name": "Sữa Similac 400g", "sku": null,     "quantity": 1, "unit_price": 200000, "amount": 200000}
  ]
}

[VÍ DỤ 5 — order / vận đơn]
{
  "doc_type": "order", "confidence": 0.85,
  "doc_date": "2024-06-20", "party_name": "Trần Thị B",
  "total_amount": 780000, "currency": "VND", "status": "dang_giao",
  "tracking_code": "GHN123456789", "note": "Gọi trước khi giao",
  "items": [
    {"product_name": "Áo thun nam size L", "sku": "AT-L", "quantity": 3, "unit_price": 250000, "amount": 750000},
    {"product_name": "Phí giao hàng",      "sku": null,   "quantity": 1, "unit_price": 30000,  "amount": 30000}
  ]
}

[VÍ DỤ 6 — ảnh không phải chứng từ]
{
  "doc_type": "unknown", "confidence": 0.05,
  "doc_date": null, "party_name": null,
  "total_amount": 0, "currency": "VND", "status": null,
  "tracking_code": null, "note": null, "items": []
}

═══ SCHEMA JSON (trả về đúng cấu trúc này) ═══

Nếu doc_type = "store_report":
{
  "doc_type": "store_report",
  "confidence": number,
  "report": {
    "report_date": "YYYY-MM-DD" | null,
    "branch": string | null,
    "channels": [
      {"channel": "cua_hang"|"grab"|"now_shopee"|"xanh"|"be",
       "revenue": int_VND, "banh_qty": number, "nuoc_qty": number}
    ],
    "totals": {
      "gross_revenue": int_VND, "cost": int_VND, "net_revenue": int_VND,
      "cash": int_VND, "transfer": int_VND, "discrepancy": int_VND
    } | null,
    "products": [
      {"name": string, "category": "banh"|"topping"|"nuoc",
       "grab": number, "now_shopee": number, "xanh": number,
       "be": number, "cua_hang": number, "total": number}
    ],
    "inventory": [
      {"name": string, "open": number, "import": number, "discard": number, "close": number}
    ]
  }
}

Nếu doc_type = "sale" hoặc "order":
{
  "doc_type": "sale" | "order",
  "confidence": number,
  "doc_date": "YYYY-MM-DD" | null,
  "party_name": string | null,
  "total_amount": number,
  "currency": "VND",
  "status": string | null,
  "tracking_code": string | null,
  "note": string | null,
  "items": [{"product_name": string, "sku": string|null, "quantity": number,
             "unit_price": number, "amount": number}]
}

Nếu doc_type = "unknown":
{
  "doc_type": "unknown",
  "confidence": number,
  "doc_date": null, "party_name": null, "total_amount": 0, "currency": "VND",
  "status": null, "tracking_code": null, "note": null, "items": []
}"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def extract_document(
    image_bytes: bytes,
    *,
    mime: str = "image/jpeg",
    model: str | None = None,
) -> dict:
    """Trích xuất thông tin có cấu trúc từ ảnh chứng từ.

    Args:
        image_bytes: Nội dung nhị phân của ảnh (JPEG, PNG) hoặc PDF.
        mime:        MIME type — ví dụ "image/jpeg", "image/png", "application/pdf".
        model:       Model override (None → dùng VISION_MODEL mặc định).
                     Chỉ dùng khi muốn ép model cụ thể từ bên ngoài.

    Returns:
        Dict chuẩn hoá với các trường doc_type, confidence, …
        Nếu doc_type="store_report": có thêm trường "report" với channels/totals/products/inventory.
        Nếu doc_type="sale"/"order": có các trường doc_date, party_name, total_amount, items, …
    """
    today = date.today().isoformat()
    user_prompt = (
        f"Hôm nay là {today}. "
        "Hãy trích xuất chứng từ trong ảnh theo đúng schema JSON đã mô tả."
    )
    fallback = _fallback_model()

    # --- Lần thử 1: model chính ---
    _used_fallback = False
    try:
        raw = await vision_json(
            image_bytes, _SYSTEM, user_prompt, mime=mime, model=model
        )
    except BeeknoeeError:
        # JSON parse thất bại → thử ngay với model mạnh hơn
        raw = await vision_json(
            image_bytes, _SYSTEM, user_prompt, mime=mime, model=fallback
        )
        _used_fallback = True

    result = _normalize(raw, today)

    # --- Lần thử 2: nếu confidence thấp và chưa dùng fallback ---
    if not _used_fallback and result["confidence"] < _CONFIDENCE_THRESHOLD:
        try:
            raw2 = await vision_json(
                image_bytes, _SYSTEM, user_prompt, mime=mime, model=fallback
            )
            result2 = _normalize(raw2, today)
            # Dùng kết quả fallback chỉ khi tốt hơn hoặc bằng kết quả gốc
            return result2 if result2["confidence"] >= result["confidence"] else result
        except BeeknoeeError:
            pass  # Giữ kết quả gốc nếu fallback cũng lỗi

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_VALID_STATUSES = frozenset({"cho_giao", "dang_giao", "da_giao", "huy"})
_VALID_CHANNELS = frozenset({"cua_hang", "grab", "now_shopee", "xanh", "be"})
_VALID_CATEGORIES = frozenset({"banh", "topping", "nuoc"})


def _to_int(val) -> int:
    """Chuyển giá trị về int VND, None/invalid → 0."""
    try:
        return int(round(float(val)))
    except (TypeError, ValueError):
        return 0


def _to_num(val) -> float:
    """Chuyển giá trị về float (số lượng), None/invalid → 0.0."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _normalize(data: dict, today: str) -> dict:
    """Chuẩn hoá và kiểm tra chất lượng kết quả từ model.

    Phân nhánh theo doc_type:
    - store_report → _normalize_store_report (schema riêng, không có total_amount/items)
    - sale / order / unknown → logic chuẩn hoá cũ (tương thích ngược)
    """
    # --- Giá trị mặc định doc_type ---
    data.setdefault("doc_type", "unknown")

    # --- store_report: xử lý riêng, không áp dụng logic sale/order ---
    if data["doc_type"] == "store_report":
        return _normalize_store_report(data, today)

    # --- Giá trị mặc định cho sale/order/unknown ---
    data.setdefault("currency", "VND")
    data.setdefault("items", [])
    data.setdefault("status", None)
    data.setdefault("tracking_code", None)
    data.setdefault("party_name", None)
    data.setdefault("note", None)

    # --- Confidence: parse + clamp ---
    try:
        conf = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))

    # Hậu kỳ: hạ confidence khi dữ liệu thiếu
    if data["doc_type"] == "unknown":
        conf = min(conf, 0.30)

    missing_total = not data.get("total_amount")  # 0 hoặc null → thiếu
    missing_items = not data.get("items")
    if missing_total and missing_items and data["doc_type"] != "unknown":
        conf = max(0.0, conf - 0.30)

    data["confidence"] = round(conf, 3)

    # --- Ngày ---
    if not data.get("doc_date"):
        data["doc_date"] = today

    # --- Chuẩn hoá total_amount → int ---
    raw_total = data.get("total_amount")
    try:
        data["total_amount"] = int(round(float(raw_total))) if raw_total is not None else 0
    except (TypeError, ValueError):
        data["total_amount"] = 0

    # --- Chuẩn hoá items ---
    clean_items: list[dict] = []
    for item in data.get("items", []):
        if not isinstance(item, dict):
            continue
        for amt_field in ("unit_price", "amount"):
            raw_val = item.get(amt_field)
            try:
                item[amt_field] = int(round(float(raw_val))) if raw_val is not None else 0
            except (TypeError, ValueError):
                item[amt_field] = 0
        item.setdefault("sku", None)
        item.setdefault("quantity", 0)
        clean_items.append(item)
    data["items"] = clean_items

    # --- Validate status ---
    if data.get("status") not in _VALID_STATUSES:
        data["status"] = None

    return data


def _normalize_store_report(data: dict, today: str) -> dict:
    """Chuẩn hoá kết quả cho doc_type=store_report.

    - Confidence: clamp 0..1, không áp dụng penalty của sale/order.
    - report.channels[].revenue → int VND (đã được model nhân ×1000).
    - report.channels[].banh_qty / nuoc_qty → float số lượng (KHÔNG nhân 1000).
    - report.totals → tất cả trường int VND.
    - report.products: category phải thuộc {"banh","topping","nuoc"}.
    - report.inventory: open/import/discard/close → float số lượng.
    - Kênh không hợp lệ bị lọc ra.
    """
    # --- Confidence ---
    try:
        conf = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    data["confidence"] = round(max(0.0, min(1.0, conf)), 3)

    # --- report section ---
    report = data.get("report")
    if not isinstance(report, dict):
        report = {}

    # report_date — giữ null khi model không đọc được ngày (KHÔNG override = today)
    report.setdefault("report_date", None)

    # branch
    report.setdefault("branch", None)

    # channels
    raw_channels = report.get("channels") or []
    clean_channels: list[dict] = []
    for ch in raw_channels:
        if not isinstance(ch, dict):
            continue
        channel_name = ch.get("channel", "")
        if channel_name not in _VALID_CHANNELS:
            continue
        clean_channels.append({
            "channel": channel_name,
            "revenue": _to_int(ch.get("revenue")),
            "banh_qty": _to_num(ch.get("banh_qty")),
            "nuoc_qty": _to_num(ch.get("nuoc_qty")),
        })
    report["channels"] = clean_channels

    # totals
    totals = report.get("totals")
    if isinstance(totals, dict):
        for key in ("gross_revenue", "cost", "net_revenue", "cash", "transfer", "discrepancy"):
            totals[key] = _to_int(totals.get(key))
        report["totals"] = totals
    else:
        report["totals"] = None

    # products
    raw_products = report.get("products") or []
    clean_products: list[dict] = []
    for p in raw_products:
        if not isinstance(p, dict):
            continue
        cat = p.get("category", "")
        if cat not in _VALID_CATEGORIES:
            continue
        if str(p.get("name", "")).strip() == "":
            continue
        clean_products.append({
            "name": str(p.get("name", "")),
            "category": cat,
            "grab": _to_num(p.get("grab")),
            "now_shopee": _to_num(p.get("now_shopee")),
            "xanh": _to_num(p.get("xanh")),
            "be": _to_num(p.get("be")),
            "cua_hang": _to_num(p.get("cua_hang")),
            "total": _to_num(p.get("total")),
        })
    report["products"] = clean_products

    # inventory
    raw_inventory = report.get("inventory") or []
    clean_inventory: list[dict] = []
    for inv in raw_inventory:
        if not isinstance(inv, dict):
            continue
        if str(inv.get("name", "")).strip() == "":
            continue
        clean_inventory.append({
            "name": str(inv.get("name", "")),
            "open": _to_num(inv.get("open")),
            "import": _to_num(inv.get("import")),
            "discard": _to_num(inv.get("discard")),
            "close": _to_num(inv.get("close")),
        })
    report["inventory"] = clean_inventory

    data["report"] = report
    return data

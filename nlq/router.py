"""Hiểu câu hỏi tiếng Việt → chọn thống kê phù hợp → trả lời.

Chiến lược 2 lớp cho an toàn & rẻ:
1. Bắt nhanh bằng từ khoá (không tốn token) cho các mẫu phổ biến.
2. Nếu không chắc, hỏi LLM phân loại thành {intent, period, limit, name_like}
   có kiểm soát (whitelist).

NGUYÊN TẮC GROUNDING (BẤT BIẾN):
  • LLM CHỈ trả {intent, period, limit, name_like} — KHÔNG sinh số hay câu tự do.
  • Mọi con số do repo (SQL) tính; answer() format tất định từ repo rows.
  • Truy vấn rỗng → câu trả lời trung thực "Chưa có dữ liệu … không suy đoán."
  • Câu hỏi ngoài phạm vi → "out_of_scope" → câu cố định.
  • Tên sản phẩm/khách chỉ dùng làm tham số filter — KHÔNG ghép vào SQL.
"""
from __future__ import annotations

import re
from datetime import date as _date

from bot import formatting as fmt
from db import repository as repo
from nlq.periods import resolve
from vision.beeknoee import text_json

# ── Whitelist intent & period ────────────────────────────────────────
INTENTS = {
    "revenue", "sellers", "top_products", "orders",
    "customers", "product", "report", "out_of_scope",
    # Sprint 3: store_report intents
    "channels", "financials", "inventory", "branches",
}
PERIODS = {
    "today", "yesterday", "this_week", "last_week",
    "this_month", "last_month", "all",
}

# Câu trả lời cố định
_OUT_OF_SCOPE_MSG = (
    "Bot chỉ trả lời từ số liệu bán hàng/đơn hàng trong nhóm. "
    "Câu hỏi này ngoài phạm vi dữ liệu mình đang có."
)
_NO_DATA_SUFFIX = " (mình chỉ trả lời từ số liệu đã lưu, không suy đoán)."

# Từ khoá tín hiệu bán hàng (heuristic helper — không dùng làm pre-filter cứng trong answer())
_SALES_SIGNAL_WORDS = frozenset([
    "doanh", "tiền", "đơn", "don", "hàng", "hang", "bán", "ban",
    "sản phẩm", "san pham", "khách", "khach", "nhân viên", "nhan vien",
    "hoá đơn", "hoa don", "tổng", "tong", "top", "báo cáo", "bao cao",
    "report", "thống kê", "thong ke", "thu", "giao", "vận", "van",
    "trạng thái", "trang thai", "còn bao nhiêu", "con bao nhieu",
    "bán chạy", "ban chay", "doanh thu", "revenue", "order", "seller",
    "tổng hợp", "tong hop",
    # Sprint 3 — store_report
    "kênh", "kenh", "tồn kho", "ton kho", "tồn", "kho",
    "tiền mặt", "tien mat", "chuyển khoản", "chuyen khoan",
    "chi phí", "chi phi", "net", "lãi", "lai gop", "lãi gộp",
    "cơ sở", "co so", "chi nhánh", "chi nhanh",
])

# Lời chào rõ ràng — có thể short-circuit mà không cần gọi LLM
_GREETING_PREFIXES = (
    "xin chào", "xin chao", "chào bot", "chao bot", "hello bot", "hi bot",
)

# Regex loại bỏ từ dừng khi trích tên sản phẩm (module-level để tránh compile lại mỗi lần)
_BRANCH_STOP_RE = re.compile(
    r"\s+(?:hôm nay|hôm qua|tuần này|tuần trước|tháng này|tháng trước"
    r"|hom nay|hom qua|tuan nay|thang nay|today|yesterday)$",
    re.IGNORECASE,
)

_STOP_RE = re.compile(
    r"\b(?:hôm nay|hôm qua|tuần này|tuần trước|tháng này|tháng trước|"
    r"tất cả|toàn bộ|hom nay|hom qua|tuan nay|tuan truoc|thang nay|thang truoc|"
    r"bao nhiêu|bao nhieu|còn không|co khong|còn|con|"
    r"bán được|ban duoc|bán đc|bán|ban|được|duoc|"
    r"sản phẩm|san pham|hỏi|hoi)\b",
    re.IGNORECASE,
)


# Compile regex một lần (module-level) để tránh FP kiểu "ban" in "banana".
# Sắp xếp từ dài trước (cụm > từ đơn) để alternation khớp đúng thứ tự.
_SALES_RE = re.compile(
    r"(?:" + "|".join(
        r"\b" + re.escape(w) + r"\b"
        for w in sorted(_SALES_SIGNAL_WORDS, key=len, reverse=True)
    ) + r")",
    re.IGNORECASE,
)


def _has_sales_signal(t: str) -> bool:
    """Heuristic: câu hỏi có từ liên quan bán hàng? So khớp theo ranh giới từ (\\b).

    Dùng regex để tránh false positive: 'ban' không khớp 'banana',
    'hang' không khớp 'change', 'don' không khớp 'donation'.
    """
    return bool(_SALES_RE.search(t))


# Danh sách chủ đề CHẮC CHẮN ngoài phạm vi bán hàng — fast-path không cần LLM.
# Hẹp có chủ đích: chỉ liệt kê topic rõ ràng, để câu hỏi mơ hồ vẫn được LLM phân loại.
_OFFTOPIC_RE = re.compile(
    r"\b(?:"
    r"thời tiết|thoi tiet|weather|"
    r"thể thao|the thao|bóng đá|bong da|"
    r"nấu ăn|nau an|ăn gì|an gi|ăn uống|an uong|"
    r"lịch sử|lich su|tin tức|tin tuc|"
    r"âm nhạc|am nhac|bài hát|bai hat|phim ảnh|phim anh"
    r")\b",
    re.IGNORECASE,
)


def _is_offtopic(q: str) -> bool:
    """Kiểm tra nhanh chủ đề ngoài phạm vi (không gọi LLM).

    Chỉ bắt các chủ đề rõ ràng (thời tiết, thể thao, …).
    Câu hỏi mơ hồ / không rõ ý vẫn được chuyển qua LLM phân loại.
    """
    return bool(_OFFTOPIC_RE.search(q))


# ── Lớp 1: từ khoá ─────────────────────────────────────────────────
def _keyword_route(q: str) -> tuple[str | None, str, int]:
    """Trả (intent | None, period_token, limit).

    Giữ nguyên 3 giá trị để tương thích với code cũ & tests hiện có.
    name_like cho intent=product được trích riêng trong answer().
    """
    t = q.lower()
    period = _keyword_period(t)

    # 1. top / bán chạy → top_products (ưu tiên cao nhất)
    if any(k in t for k in ("top", "bán chạy", "ban chay")):
        return "top_products", period, _extract_int(t, default=5)

    # 2. theo khách / khách hàng → customers
    if any(k in t for k in ("theo khách", "theo khach", "khách hàng", "khach hang")):
        return "customers", period, 5

    # 3. báo cáo / tổng hợp → report
    if any(k in t for k in ("báo cáo", "bao cao", "tổng hợp", "tong hop", "report")):
        return "report", period, 5

    # 4. đơn hàng / vận đơn / trạng thái → orders
    if any(k in t for k in (
        "đơn hàng", "don hang", "vận đơn", "van don",
        "trạng thái", "trang thai", "chờ giao", "cho giao", "đã giao", "da giao",
    )):
        return "orders", period, 5

    # 5. theo người / nhân viên / ai bán → sellers
    if any(k in t for k in (
        "theo người", "theo nguoi", "nhân viên", "nhan vien",
        "ai bán", "ai ban",
    )):
        return "sellers", period, 5

    # 6. sản phẩm [X] / còn bao nhiêu [X] → product
    #    Ưu tiên TRƯỚC "bán được" để "sản phẩm X bán được bao nhiêu" → product
    if any(k in t for k in ("còn bao nhiêu", "con bao nhieu", "sản phẩm", "san pham")):
        return "product", period, 5

    # 7. tồn kho → inventory (Sprint 3)
    if any(k in t for k in ("tồn kho", "ton kho", "kho tồn", "kho ton", "tồn hàng", "ton hang")):
        return "inventory", period, 5

    # 8. doanh thu theo kênh / kênh bán → channels (Sprint 3)
    if any(k in t for k in (
        "theo kênh", "theo kenh", "doanh thu kênh", "doanh thu kenh",
        "kênh bán", "kenh ban",
    )):
        return "channels", period, 5

    # 9. tài chính / net / tiền mặt / chuyển khoản / chi phí → financials (Sprint 3)
    if any(k in t for k in (
        "tài chính", "tai chinh", "tiền mặt", "tien mat",
        "chuyển khoản", "chuyen khoan", "chi phí", "chi phi",
        "lãi gộp", "lai gop", "lãi ròng", "lai rong",
    )) or re.search(r"\bnet\b", t):
        return "financials", period, 5

    # 10. danh sách cơ sở / chi nhánh → branches (Sprint 3)
    if any(k in t for k in (
        "danh sách cơ sở", "danh sach co so", "danh sách chi nhánh", "danh sach chi nhanh",
        "các cơ sở", "cac co so", "list cơ sở",
    )):
        return "branches", period, 5

    # 11. doanh thu / tổng tiền / bán được (chung, không có product cụ thể) → revenue
    if any(k in t for k in (
        "doanh thu", "doanh so", "doanh số", "tổng tiền", "tong tien",
        "thu về", "thu ve", "bán được", "ban duoc",
    )):
        return "revenue", period, 5

    return None, period, 5


def _keyword_period(t: str) -> str:
    """Trích period token từ câu hỏi.

    Trả về:
      • Canonical tokens: "today", "yesterday", "this_week", "last_week",
        "this_month", "last_month", "all"
      • Structured tokens mới: "range:YYYY-MM-DD:YYYY-MM-DD",
        "tháng:M", "quý:Q"
    """
    today = _date.today()

    # Ưu tiên 1: "từ dd/mm[/yyyy] đến dd/mm[/yyyy]"
    m = re.search(
        r"từ\s+(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s+đến\s+(\d{1,2}/\d{1,2}(?:/\d{2,4})?)",
        t,
    )
    if m:
        start = _parse_vn_date(m.group(1), today.year)
        end = _parse_vn_date(m.group(2), today.year)
        if start and end and start <= end:
            return f"range:{start.isoformat()}:{end.isoformat()}"

    # Ưu tiên 2: check "tháng này"/"tháng trước" trước khi check "tháng M"
    for kw, val in (
        ("hôm qua", "yesterday"), ("hom qua", "yesterday"),
        ("tuần này", "this_week"), ("tuan nay", "this_week"),
        ("tuần trước", "last_week"), ("tuan truoc", "last_week"),
        ("tháng này", "this_month"), ("thang nay", "this_month"),
        ("tháng trước", "last_month"), ("thang truoc", "last_month"),
        ("tất cả", "all"), ("tat ca", "all"), ("toàn bộ", "all"),
        ("hôm nay", "today"), ("hom nay", "today"),
    ):
        if kw in t:
            return val

    # Ưu tiên 3: "tháng M" (số cụ thể, VD: tháng 3, tháng 12)
    m = re.search(r"tháng\s+(\d{1,2})", t)
    if not m:
        m = re.search(r"thang\s+(\d{1,2})", t)
    if m:
        month = int(m.group(1))
        if 1 <= month <= 12:
            return f"tháng:{month}"

    # Ưu tiên 4: "quý Q" (quý 1 → 4)
    m = re.search(r"quý\s+(\d)", t)
    if not m:
        m = re.search(r"quy\s+(\d)", t)
    if m:
        q = int(m.group(1))
        if 1 <= q <= 4:
            return f"quý:{q}"

    # Ưu tiên 5: ngày lẻ "ngày 19/07", "19/07", "19/07/2026"
    # Chỉ bắt sau khi range & keyword đã xử lý → không conflict
    m = re.search(
        r"(?:^|\s)(?:ngày\s+)?(\d{1,2}/\d{1,2}(?:/\d{2,4})?)(?=\s|$)",
        t,
    )
    if m:
        d = _parse_vn_date(m.group(1), today.year)
        if d:
            return f"day:{d.isoformat()}"

    return "today"


def _parse_vn_date(s: str, default_year: int):
    """Parse ngày dạng dd/mm hoặc dd/mm/yyyy → date object. Trả None nếu không hợp lệ."""
    parts = s.strip().split("/")
    try:
        if len(parts) == 2:
            day, month = int(parts[0]), int(parts[1])
            year = default_year
        elif len(parts) == 3:
            day, month = int(parts[0]), int(parts[1])
            year = int(parts[2])
            if year < 100:
                year += 2000
        else:
            return None
        return _date(year, month, day)
    except (ValueError, OverflowError):
        return None


def _extract_int(t: str, default: int = 5) -> int:
    """Trích số nguyên cho 'limit' — ưu tiên "top N"; bỏ qua số đứng cạnh '/'.

    Tránh bắt nhầm ngày tháng: "từ 01/01 đến 31/01" → trả default thay vì 1.
    """
    # Ưu tiên 1: số ngay sau "top" (vd "top 5", "top 10 sản phẩm")
    m = re.search(r"\btop\s+(\d{1,2})\b", t)
    if m:
        return int(m.group(1))
    # Fallback: số nguyên độc lập không đứng cạnh '/' (loại số trong ngày/tháng)
    m = re.search(r"(?<!/)\b(\d{1,3})\b(?!/)", t)
    return int(m.group(1)) if m else default


def _extract_branch(q: str) -> str | None:
    """Trích tên cơ sở từ câu hỏi.

    Nhận diện "cơ sở X", "chi nhánh X", "co so X" (X là phần tiếp theo, tối đa 3 từ).
    Trả về tên đầy đủ có prefix: "cơ sở X" → "Cơ sở X", "chi nhánh X" → "X".
    An toàn: chỉ lấy chuỗi, KHÔNG sinh dữ liệu.
    """
    patterns = [
        # "cơ sở X" → giữ prefix "Cơ sở" để khớp với caption lưu trong DB
        (r"(?:cơ sở|co so)\s+([^\s,;?!]+(?:\s+[^\s,;?!]+){0,2})", "Cơ sở"),
        # "chi nhánh X" → lấy phần X (tên chi nhánh thường là địa danh)
        (r"(?:chi nhánh|chi nhanh)\s+([^\s,;?!]+(?:\s+[^\s,;?!]+){0,2})", None),
    ]
    for pat, prefix in patterns:
        m = re.search(pat, q, re.IGNORECASE)
        if m:
            tail = _BRANCH_STOP_RE.sub("", m.group(1).strip()).strip()
            if not tail:
                continue
            branch = f"{prefix} {tail}" if prefix else tail
            return branch[:50]
    return None


def _extract_name_like(t: str) -> str | None:
    """Trích tên sản phẩm từ câu hỏi cho intent=product.

    An toàn: chỉ lấy chuỗi, KHÔNG sinh dữ liệu.
    Dùng làm tham số LIKE filter — không bao giờ ghép trực tiếp vào SQL.
    """
    # Triggers: (chuỗi kích hoạt)
    triggers = [
        "sản phẩm",
        "san pham",
        "còn bao nhiêu",
        "con bao nhieu",
        "bán được bao nhiêu",
        "ban duoc bao nhieu",
    ]

    for trigger in triggers:
        idx = t.find(trigger)
        if idx >= 0:
            after = t[idx + len(trigger):].strip()
            # Xoá từ stop và từ khoá query
            after = _STOP_RE.sub("", after).strip()
            # Xoá dấu câu đầu/cuối và khoảng trắng thừa
            after = re.sub(r"[?!,.:;]+", "", after).strip()
            after = re.sub(r"\s+", " ", after).strip()
            if after:
                return after[:100]  # giới hạn an toàn

    return None


# ── Lớp 2: LLM phân loại (fallback) ────────────────────────────────
_SYSTEM = """\
Bạn phân loại câu hỏi thống kê tiếng Việt cho một bot bán hàng.
Trả về DUY NHẤT JSON: {"intent": ..., "period": ..., "limit": số, "name_like": ...}.

intent ∈ ["revenue","sellers","top_products","orders","customers","product","report",
          "channels","financials","inventory","branches","out_of_scope"].
  - revenue: hỏi về tổng doanh thu / tổng tiền
  - sellers: hỏi doanh thu theo từng người bán
  - top_products: hỏi sản phẩm bán chạy nhất
  - orders: hỏi về đơn hàng / trạng thái giao hàng
  - customers: hỏi doanh thu theo từng khách hàng
  - product: hỏi về một sản phẩm cụ thể (số lượng, doanh thu)
  - report: hỏi báo cáo tổng hợp / full report
  - channels: hỏi doanh thu theo kênh bán (Grab, Now, cửa hàng, …)
  - financials: hỏi net / lãi gộp / tiền mặt / chuyển khoản / chi phí
  - inventory: hỏi tồn kho (số lượng tồn, nhập, xuất, huỷ)
  - branches: hỏi danh sách cơ sở / chi nhánh
  - out_of_scope: câu hỏi KHÔNG liên quan đến bán hàng/đơn hàng

period ∈ ["today","yesterday","this_week","last_week","this_month","last_month","all"].
limit: số nguyên (mặc định 5, tối đa 20).
name_like: chuỗi tên sản phẩm nếu intent="product", ngược lại null.

KHÔNG giải thích. KHÔNG sinh số. Chỉ trả JSON.\
"""


async def _llm_route(question: str) -> tuple[str, str, int, str | None]:
    """Gọi LLM phân loại intent — luôn trả 4 giá trị (intent, period, limit, name_like).

    Validate chặt: intent ngoài whitelist → out_of_scope; period ngoài whitelist → today.
    """
    try:
        data = await text_json(_SYSTEM, question)
    except Exception:
        # LLM không khả dụng → fail-safe: out_of_scope (trung thực, không phịa doanh thu).
        # Câu bán hàng rõ ràng đã được _keyword_route bắt trước khi tới đây.
        return "out_of_scope", "today", 5, None

    # Validate intent
    raw_intent = data.get("intent", "")
    intent = raw_intent if raw_intent in INTENTS else "out_of_scope"

    # Validate period
    raw_period = data.get("period", "")
    period = raw_period if raw_period in PERIODS else "today"

    # Validate limit
    try:
        limit = int(data.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(limit, 20))

    # Validate name_like (chỉ dùng làm tham số, không ghép SQL)
    raw_name = data.get("name_like")
    name_like: str | None = (str(raw_name).strip()[:100] or None) if raw_name else None

    return intent, period, limit, name_like


# ── Câu trả lời chính ───────────────────────────────────────────────
async def answer(question: str, group_id: str | None) -> str:
    """Phân tích câu hỏi và trả lời từ dữ liệu repo.

    GROUNDING: mọi con số đến từ SQL — LLM KHÔNG sinh số.
    Truy vấn rỗng → câu trả lời trung thực.
    """
    intent, period, limit = _keyword_route(question)
    name_like: str | None = None

    if intent is None:
        # Fast-path lớp 1a: lời chào rõ ràng → out_of_scope, không cần LLM
        _t = question.lower()
        if any(g in _t for g in _GREETING_PREFIXES):
            return _OUT_OF_SCOPE_MSG
        # Fast-path lớp 1b (tất định, không tốn LLM): chủ đề ngoài phạm vi rõ ràng
        # → trả out_of_scope ngay mà không cần gọi LLM
        if _is_offtopic(question):
            return _OUT_OF_SCOPE_MSG
        # Không rõ intent, nhưng có thể là câu bán hàng → hỏi LLM phân loại
        _kw_period = period  # lưu period đã trích từ keyword (có thể là ngày cụ thể)
        intent, period, limit, name_like = await _llm_route(question)
        # LLM chỉ biết canonical periods (today/yesterday/…) không biết "day:YYYY-MM-DD"
        # → khôi phục period từ keyword nếu cụ thể hơn (không phải mặc định "today")
        if _kw_period != "today":
            period = _kw_period

    # out_of_scope → câu trả lời cố định
    if intent == "out_of_scope":
        return _OUT_OF_SCOPE_MSG

    # Với product intent, trích tên sản phẩm (kể cả từ LLM route nếu chưa có)
    if intent == "product" and name_like is None:
        name_like = _extract_name_like(question.lower())
        if name_like is None:
            # F2: không rõ tên sản phẩm → hỏi lại, KHÔNG gọi repo với None
            return (
                'Bạn hỏi về sản phẩm nào? Vui lòng nêu tên cụ thể'
                ' (vd: "sản phẩm áo thun tháng này").'
            )

    # Trích branch từ câu hỏi (Sprint 3 — store_report intents)
    branch = _extract_branch(question)

    # Giải mã period → (start, end) + label
    (start, end), label = resolve(period)

    # ── Dispatch theo intent ────────────────────────────────────────
    if intent == "sellers":
        rows = repo.revenue_by_seller(group_id, start, end)
        return fmt.sellers_block(label, rows)

    if intent == "customers":
        rows = repo.revenue_by_customer(group_id, start, end, limit)
        return fmt.customers_block(label, rows)

    if intent == "product":
        name_hint = f" '{name_like}'" if name_like else ""
        _no_product = f"Chưa có dữ liệu sản phẩm{name_hint} {label}{_NO_DATA_SUFFIX}"
        data = repo.product_detail(group_id, start, end, name_like)
        # Chuẩn hoá về single dict; product_detail_block nhận một dict
        if isinstance(data, list):
            d = data[0] if data else None
        elif isinstance(data, dict) and data:
            d = data
        else:
            d = None
        if d is None:
            return _no_product
        return fmt.product_detail_block(label, d)

    if intent == "report":
        # ── Dữ liệu sale/order (cũ) ──────────────────────────────────
        data = repo.full_report(group_id, start, end)
        rev = data.get("revenue", {}) if isinstance(data, dict) else {}
        has_sale_data = (
            bool(rev.get("count"))
            or bool(data.get("top_products"))
            or bool(data.get("orders"))
        )

        # ── Dữ liệu store_report (Sprint 3) ─────────────────────────
        _ch_fn = getattr(repo, "revenue_by_channel", None)
        _fin_fn = getattr(repo, "report_financials", None)
        _ps_fn = getattr(repo, "product_sales_report", None)
        channels = _ch_fn(group_id, start, end) if _ch_fn else None
        financials = _fin_fn(group_id, start, end) if _fin_fn else None
        has_store_data = bool(channels) or (
            bool(financials) and financials.get("count", 0) > 0
        )

        if not has_sale_data and not has_store_data:
            return f"Chưa có dữ liệu báo cáo {label}{_NO_DATA_SUFFIX}"

        parts: list[str] = []
        if has_store_data:
            if channels:
                parts.append(fmt.channels_block(label, channels))
            if financials and financials.get("count", 0) > 0:
                parts.append(fmt.financials_block(label, financials))
            if _ps_fn:
                ps_rows = _ps_fn(group_id, start, end, limit=5)
                if ps_rows:
                    parts.append(fmt.product_sales_block(label, ps_rows))
        if has_sale_data:
            parts.append(fmt.report_block(label, data))
        return "\n\n".join(parts)

    if intent == "top_products":
        rows = repo.top_products(group_id, start, end, limit)
        return fmt.top_products_block(label, rows)

    if intent == "orders":
        rows = repo.orders_by_status(group_id, start, end)
        return fmt.orders_block(label, rows)

    # ── Sprint 3: store_report intents ─────────────────────────────
    if intent == "channels":
        _fn = getattr(repo, "revenue_by_channel", None)
        if _fn is None:
            return f"Chưa có dữ liệu kênh bán {label}{_NO_DATA_SUFFIX}"
        rows = _fn(group_id, start, end, branch=branch)
        if not rows:
            return fmt.no_data(f"kênh bán {label}")
        return fmt.channels_block(label, rows, branch=branch)

    if intent == "financials":
        _fn = getattr(repo, "report_financials", None)
        if _fn is None:
            return f"Chưa có dữ liệu tài chính {label}{_NO_DATA_SUFFIX}"
        data = _fn(group_id, start, end, branch=branch)
        if not data or data.get("count", 0) == 0:
            return fmt.no_data(f"tài chính {label}")
        return fmt.financials_block(label, data, branch=branch)

    if intent == "inventory":
        _fn = getattr(repo, "inventory_latest", None)
        if _fn is None:
            return f"Chưa có dữ liệu tồn kho{_NO_DATA_SUFFIX}"
        rows = _fn(group_id, branch=branch)
        if not rows:
            return fmt.no_data("tồn kho")
        return fmt.inventory_block(rows, branch=branch)

    if intent == "branches":
        _fn = getattr(repo, "list_branches", None)
        if _fn is None:
            return f"Chưa có dữ liệu cơ sở{_NO_DATA_SUFFIX}"
        rows = _fn(group_id, start=start, end=end)
        if not rows:
            return fmt.no_data("cơ sở")
        return fmt.branches_block(rows)

    # Mặc định: revenue — gộp sale/order + store_report gross
    summary = repo.revenue_summary(group_id, start, end)
    sale_total = summary.get("total", 0) or 0
    sale_count = summary.get("count", 0) or 0

    _fin_fn = getattr(repo, "report_financials", None)
    financials = _fin_fn(group_id, start, end) if _fin_fn else None
    store_gross = (financials or {}).get("gross", 0) or 0
    store_count = (financials or {}).get("count", 0) or 0

    has_store = store_count > 0 or store_gross > 0

    if not has_store:
        return fmt.revenue_block(label, summary)

    combined = sale_total + store_gross
    lines = [f"📊 Doanh thu {label}", f"• Tổng: {fmt.vnd(combined)}"]
    if sale_count or sale_total:
        lines.append(f"  - Bán lẻ (sale): {fmt.vnd(sale_total)} ({sale_count} hoá đơn)")
    lines.append(f"  - Báo cáo cửa hàng: {fmt.vnd(store_gross)}")
    return "\n".join(lines)

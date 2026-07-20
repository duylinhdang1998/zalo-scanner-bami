"""Xử lý message từ Zalo: quét ảnh, lệnh thống kê, hỏi tự do."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from collections import defaultdict, deque

from bot import formatting as fmt
from bot import zalo_compat as z
from config.settings import settings
from db import repository as repo
from nlq import router
from vision.extractor import extract_document

log = logging.getLogger("bot")

HELP = (
    "🤖 Bot quét & thống kê bán hàng\n\n"
    "📷 Gửi ảnh hoá đơn/đơn hàng (kèm @bot hoặc caption) → bot tự lưu.\n"
    "📊 Gửi ảnh báo cáo cửa hàng qua DM (kèm tên cơ sở làm caption) → lưu kho chung.\n\n"
    "Lệnh:\n"
    "• /thongke [hôm nay|tuần này|tháng này] — tổng quan\n"
    "• /doanhthu [khoảng] — doanh thu\n"
    "• /top [n] — sản phẩm bán chạy\n"
    "• /donhang [khoảng] — đơn theo trạng thái\n"
    "• /baocao [khoảng|ngày dd/mm] — báo cáo tổng hợp\n"
    "• /khach [khoảng] — top khách hàng theo doanh thu\n"
    "• /kenh [khoảng] — doanh thu theo kênh bán\n"
    "• /tonkho — tồn kho mới nhất\n"
    "• /coso — danh sách cơ sở\n"
    "• /xoa <id> — xoá 1 bản ghi sai\n\n"
    "Hoặc tag @bot rồi hỏi tự do, vd:\n"
    "  \"@bot doanh thu kênh Grab tháng này\"\n"
    "  \"@bot tồn kho cơ sở 2\""
)

_SCAN_TRIGGERS = ("@bot", "lưu", "luu", "quét", "quet", "scan", "chốt", "chot")

# ── Rate limiting (in-memory, per user, per minute) ─────────────────────────
_SCAN_RATE_PER_MIN: int = int(os.getenv("SCAN_RATE_PER_MIN", "20"))
# {user_id: deque of monotonic timestamps (float)}
_rate_buckets: dict[str, deque[float]] = defaultdict(deque)


def _check_rate_limit(user_id: str | None) -> bool:
    """True → dưới ngưỡng (ghi timestamp); False → vượt ngưỡng.

    Dùng time.monotonic() thay datetime.utcnow() để tránh lệch múi giờ
    và không phụ thuộc đồng hồ hệ thống (deprecated trong Python 3.12+).
    """
    if not user_id:
        return True
    now = time.monotonic()
    bucket = _rate_buckets[user_id]
    # Loại bỏ timestamp cũ hơn 60 giây
    while bucket and bucket[0] < now - 60.0:
        bucket.popleft()
    if len(bucket) >= _SCAN_RATE_PER_MIN:
        return False
    bucket.append(now)
    return True


# ── Gộp nhiều ảnh cùng lượt gửi thành 1 báo cáo cửa hàng ────────────────────
# Báo cáo cửa hàng thường bị chụp tách nhiều ảnh (doanh thu / số lượng / tồn kho).
# Ảnh gửi liên tiếp bởi CÙNG người trong CÙNG nhóm, trong report_merge_window_sec,
# sẽ ghép chung vào 1 báo cáo. State chỉ trong RAM (bot 1 tiến trình) — mất khi
# restart cũng không sao (tệ nhất là tách thành báo cáo mới).
# {(group_id, sender_id): (report_id, monotonic_ts)}
_report_sessions: dict[tuple[str | None, str | None], tuple[int, float]] = {}


def _merge_target(gid: str | None, uid: str | None) -> int | None:
    """Trả report_id đang mở của (nhóm, người gửi) nếu còn trong cửa sổ, else None."""
    window = settings.report_merge_window_sec
    if window <= 0:
        return None
    entry = _report_sessions.get((gid, uid))
    if entry is None:
        return None
    report_id, ts = entry
    if time.monotonic() - ts > window:
        _report_sessions.pop((gid, uid), None)
        return None
    return report_id


def _remember_report(gid: str | None, uid: str | None, report_id: int | None) -> None:
    """Ghi nhớ báo cáo vừa lưu/ghép để ảnh kế tiếp cùng lượt gộp vào."""
    if settings.report_merge_window_sec <= 0 or not report_id:
        return
    _report_sessions[(gid, uid)] = (report_id, time.monotonic())


# ── Helper reply ─────────────────────────────────────────────────────────────
async def _reply(update, text: str) -> None:
    msg = getattr(update, "message", None)
    if msg and hasattr(msg, "reply_text"):
        try:
            await msg.reply_text(text)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "reply_text thất bại (update=%r): %s",
                type(update).__name__,
                exc,
                exc_info=True,
            )


# ── Ảnh ──────────────────────────────────────────────────────────────────────
async def on_photo(update, context) -> None:
    """Xử lý ảnh gửi vào nhóm / chat 1-1."""
    # [DIAG] Log thô để biết Zalo có đẩy ảnh (không mention) cho bot không
    log.info(
        "RAW on_photo | group=%s is_group=%s has_photo=%s caption=%r",
        z.chat_id(update), z.is_group(update), z.has_photo(update),
        z.message_text(update)[:40],
    )
    # Sticker không phải ảnh thật
    if z.is_sticker(update):
        return

    if not _should_scan(update):
        return

    uid, name = z.sender(update)
    gid = z.chat_id(update)

    # Rate limit: bảo vệ khỏi spam ảnh
    if not _check_rate_limit(uid):
        log.info(
            "on_photo | group=%s sender=%s — đã chặn (vượt %d ảnh/phút)",
            gid, uid, _SCAN_RATE_PER_MIN,
        )
        await _reply(
            update,
            f"⏳ Bạn gửi ảnh hơi nhanh rồi! Vui lòng chờ chút rồi gửi lại nhé. "
            f"(Giới hạn {_SCAN_RATE_PER_MIN} ảnh/phút)",
        )
        return

    # Tải ảnh
    try:
        img, mime = await z.photo_bytes(update, context)
    except ValueError as e:
        # Lỗi business rõ ràng (kích thước, content-type)
        log.warning("on_photo | group=%s sender=%s — tải ảnh từ chối: %s", gid, uid, e)
        await _reply(update, f"⚠️ {e}")
        return
    except Exception as e:  # noqa: BLE001
        log.warning("on_photo | group=%s sender=%s — tải ảnh lỗi: %s", gid, uid, e)
        await _reply(update, "⚠️ Không tải được ảnh. Kiểm tra kết nối rồi gửi lại giúp mình nhé.")
        return

    log.info(
        "on_photo | group=%s sender=%s — tải ảnh OK (%d bytes, %s), bắt đầu đọc",
        gid, uid, len(img), mime,
    )
    await _reply(update, "🔎 Đang đọc ảnh…")

    # Trích xuất dữ liệu
    try:
        data = await extract_document(img, mime=mime)
    except Exception as e:  # noqa: BLE001
        log.exception("on_photo | group=%s sender=%s — extract_document lỗi", gid, uid)
        await _reply(
            update,
            "⚠️ Đọc ảnh thất bại — mình chưa hiểu được nội dung. "
            "Thử chụp rõ hơn rồi gửi lại nhé.",
        )
        return

    doc_type = data.get("doc_type", "unknown")
    confidence = data.get("confidence", 0)

    if doc_type == "unknown":
        log.info(
            "on_photo | group=%s sender=%s — doc_type=unknown, bỏ qua",
            gid, uid,
        )
        await _reply(update, "🤔 Ảnh này không giống hoá đơn/đơn hàng. Bỏ qua.")
        return

    if confidence < settings.confirm_threshold:
        log.info(
            "on_photo | group=%s sender=%s — confidence=%.2f thấp hơn ngưỡng %.2f, vẫn lưu",
            gid, uid, confidence, settings.confirm_threshold,
        )
        await _reply(
            update,
            "⚠️ Ảnh hơi khó đọc (độ tin cậy thấp), vẫn tạm lưu.\n"
            "Kiểm tra lại và /xoa nếu sai giúp mình.",
        )

    # Tính hash ảnh để dedup
    image_hash = hashlib.sha256(img).hexdigest()

    # ── Nhánh store_report (Sprint 3) ────────────────────────────────
    if doc_type == "store_report":
        caption = z.message_text(update).strip() or None
        report_data = data.get("report") or {}
        branch = caption if caption else report_data.get("branch")
        log.info(
            "on_photo store_report | group=%s sender=%s — branch=%r date=%r",
            gid, uid, branch, report_data.get("report_date"),
        )
        _save_fn = getattr(repo, "save_store_report", None)
        if _save_fn is None:
            log.warning("on_photo store_report | save_store_report chưa có trong repo")
            await _reply(update, "⚠️ Chức năng lưu báo cáo cửa hàng chưa sẵn sàng. Thử lại sau.")
            return
        # Ảnh gửi liên tiếp cùng lượt → ghép vào báo cáo đang mở (nếu có)
        merge_target = _merge_target(gid, uid)
        try:
            result = _save_fn(
                group_id=gid,
                sender_id=uid,
                sender_name=name,
                image_url=z.photo_url(update),
                image_hash=image_hash,
                data=data,
                branch_override=caption,
                merge_report_id=merge_target,
            )
        except Exception:  # noqa: BLE001
            log.exception("on_photo store_report | group=%s sender=%s — save lỗi", gid, uid)
            await _reply(update, "⚠️ Đọc báo cáo xong nhưng lưu dữ liệu bị lỗi. Thử lại sau nhé.")
            return
        if result.is_duplicate:
            log.info(
                "on_photo store_report | group=%s sender=%s — trùng, bỏ qua",
                gid, uid,
            )
            await _reply(update, "♻️ Báo cáo này đã lưu rồi.")
            return
        _remember_report(gid, uid, getattr(result.document, "id", None))
        log.info(
            "on_photo store_report | group=%s sender=%s — %s OK id=%s",
            gid, uid, "ghép" if result.is_merged else "lưu",
            getattr(result.document, "id", "?"),
        )
        if result.is_merged:
            await _reply(update, fmt.store_report_merged_block(report_data, result.document))
        else:
            await _reply(update, fmt.store_report_saved_block(report_data, result.document, branch))
        return
    # ─────────────────────────────────────────────────────────────────

    # Lưu vào DB (dùng save_extraction_v2 để nhận biết duplicate)
    try:
        result = repo.save_extraction_v2(
            group_id=gid,
            sender_id=uid,
            sender_name=name,
            image_url=z.photo_url(update),
            data=data,
            image_hash=image_hash,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("on_photo | group=%s sender=%s — save_extraction_v2 lỗi", gid, uid)
        await _reply(
            update,
            "⚠️ Đọc ảnh xong nhưng lưu dữ liệu bị lỗi. "
            "Thử lại sau hoặc liên hệ admin nhé.",
        )
        return

    if result.is_duplicate:
        log.info(
            "on_photo | group=%s sender=%s — ảnh trùng, doc_id=%s, bỏ qua",
            gid, uid, getattr(result.document, "id", "?"),
        )
        await _reply(
            update,
            f"♻️ Ảnh này đã được lưu trước đó (#{result.document.id}). Bỏ qua.",
        )
        return

    doc = result.document
    log.info(
        "on_photo | group=%s sender=%s — lưu OK doc_id=%s doc_type=%s confidence=%.2f",
        gid, uid, getattr(doc, "id", "?"), doc_type, confidence,
    )
    await _reply(update, fmt.saved_block(doc_type, doc))


def _should_scan(update) -> bool:
    """Quyết định có nên quét ảnh không (dựa theo scan_mode).

    - auto: luôn quét.
    - mention (mặc định):
        - Chat 1-1 và không có caption → quét.
        - Nhóm: cần caption chứa trigger word hoặc @bot.
        - Caption rỗng trong nhóm → KHÔNG quét (tránh mọi ảnh không liên quan).
    """
    if settings.scan_mode == "auto":
        return True

    text = z.message_text(update).lower()
    in_group = z.is_group(update)

    if not in_group:
        # Chat 1-1: bất kỳ ảnh nào đều quét
        return True

    # Nhóm: caption rỗng → bỏ qua
    if not text:
        return False

    # Nhóm: kiểm tra trigger word
    return any(t in text for t in _SCAN_TRIGGERS)


# ── Lệnh ─────────────────────────────────────────────────────────────────────
async def cmd_start(update, context) -> None:
    await _reply(update, HELP)


async def cmd_help(update, context) -> None:
    await _reply(update, HELP)


async def cmd_thongke(update, context) -> None:
    gid = z.chat_id(update)
    try:
        arg = _arg(update)
        from nlq.periods import resolve

        (start, end), label = resolve(_period_word(arg))
        rev = repo.revenue_summary(gid, start, end)
        tops = repo.top_products(gid, start, end, 3)
        orders = repo.orders_by_status(gid, start, end)
        blocks = [fmt.revenue_block(label, rev)]
        if tops:
            blocks.append(fmt.top_products_block(label, tops))
        if orders:
            blocks.append(fmt.orders_block(label, orders))
        await _reply(update, "\n\n".join(blocks))
    except Exception:  # noqa: BLE001
        log.exception("cmd_thongke | group=%s — lỗi", gid)
        await _reply(update, "⚠️ Lỗi khi lấy thống kê. Thử lại sau nhé.")


async def cmd_doanhthu(update, context) -> None:
    gid = z.chat_id(update)
    try:
        await _reply(update, await router.answer("doanh thu " + _arg(update), gid))
    except Exception:  # noqa: BLE001
        log.exception("cmd_doanhthu | group=%s — lỗi", gid)
        await _reply(update, "⚠️ Không lấy được doanh thu lúc này. Thử lại sau nhé.")


async def cmd_top(update, context) -> None:
    gid = z.chat_id(update)
    try:
        await _reply(update, await router.answer("top " + _arg(update), gid))
    except Exception:  # noqa: BLE001
        log.exception("cmd_top | group=%s — lỗi", gid)
        await _reply(update, "⚠️ Không lấy được top sản phẩm lúc này. Thử lại sau nhé.")


async def cmd_donhang(update, context) -> None:
    gid = z.chat_id(update)
    try:
        await _reply(update, await router.answer("đơn hàng " + _arg(update), gid))
    except Exception:  # noqa: BLE001
        log.exception("cmd_donhang | group=%s — lỗi", gid)
        await _reply(update, "⚠️ Không lấy được đơn hàng lúc này. Thử lại sau nhé.")


async def cmd_xoa(update, context) -> None:
    gid = z.chat_id(update)
    try:
        arg = _arg(update).strip().lstrip("#")
        if not arg.isdigit():
            await _reply(update, "Cú pháp: /xoa <id>. Vd: /xoa 12")
            return
        rid = int(arg)
        # Ưu tiên xoá BÁO CÁO CỬA HÀNG (store_reports) — dữ liệu chính hiện tại;
        # nếu không có thì mới thử bảng documents (hoá đơn/đơn hàng cũ).
        _del_rpt = getattr(repo, "delete_store_report", None)
        if _del_rpt is not None and _del_rpt(rid, gid):
            await _reply(update, f"🗑️ Đã xoá báo cáo #{rid} (kèm kênh/sản phẩm/tồn kho).")
        elif repo.delete_document(rid, gid):
            await _reply(update, f"🗑️ Đã xoá bản ghi #{rid}.")
        else:
            await _reply(update, f"Không thấy báo cáo/bản ghi #{rid}.")
    except Exception:  # noqa: BLE001
        log.exception("cmd_xoa | group=%s — lỗi", gid)
        await _reply(update, "⚠️ Xoá không thành công. Thử lại sau nhé.")


async def cmd_baocao(update, context) -> None:
    """Báo cáo tổng hợp: store_report (kênh + tài chính) + doanh thu + đơn hàng + sản phẩm."""
    gid = z.chat_id(update)
    try:
        arg = _arg(update)
        from nlq.periods import resolve

        (start, end), label = resolve(_period_word(arg))

        # ── Dữ liệu sale/order (cũ) ──────────────────────────────────
        report = repo.full_report(gid, start, end)
        rev = report.get("revenue") or {}
        has_sale_data = (
            rev.get("count", 0) > 0
            or report.get("top_products")
            or report.get("orders")
        )

        # ── Dữ liệu store_report (Sprint 3) ─────────────────────────
        _ch_fn = getattr(repo, "revenue_by_channel", None)
        _fin_fn = getattr(repo, "report_financials", None)
        _ps_fn = getattr(repo, "product_sales_report", None)
        channels = _ch_fn(gid, start, end) if _ch_fn else None
        financials = _fin_fn(gid, start, end) if _fin_fn else None
        has_store_data = bool(channels) or (
            bool(financials) and financials.get("count", 0) > 0
        )

        if not has_sale_data and not has_store_data:
            await _reply(update, fmt.no_data(label))
            return

        parts: list[str] = []
        if has_store_data:
            if channels:
                parts.append(fmt.channels_block(label, channels))
            if financials and financials.get("count", 0) > 0:
                parts.append(fmt.financials_block(label, financials))
            if _ps_fn:
                ps_rows = _ps_fn(gid, start, end, limit=5)
                if ps_rows:
                    parts.append(fmt.product_sales_block(label, ps_rows))
        if has_sale_data:
            parts.append(fmt.report_block(label, report))
        await _reply(update, "\n\n".join(parts))
    except Exception:  # noqa: BLE001
        log.exception("cmd_baocao | group=%s — lỗi", gid)
        await _reply(update, "⚠️ Lỗi khi tạo báo cáo. Thử lại sau nhé.")


async def cmd_khach(update, context) -> None:
    """Top khách hàng theo doanh thu trong khoảng thời gian."""
    gid = z.chat_id(update)
    try:
        arg = _arg(update)
        from nlq.periods import resolve

        (start, end), label = resolve(_period_word(arg))
        rows = repo.revenue_by_customer(gid, start, end, 5)
        if not rows:
            await _reply(update, fmt.no_data(f"khách hàng {label}"))
        else:
            await _reply(update, fmt.customers_block(label, rows))
    except Exception:  # noqa: BLE001
        log.exception("cmd_khach | group=%s — lỗi", gid)
        await _reply(update, "⚠️ Không lấy được dữ liệu khách hàng. Thử lại sau nhé.")


async def cmd_kenh(update, context) -> None:
    """Doanh thu theo kênh bán (Grab, Now, cửa hàng, …) trong khoảng thời gian."""
    gid = z.chat_id(update)
    try:
        arg = _arg(update)
        from nlq.periods import resolve

        (start, end), label = resolve(_period_word(arg))
        _fn = getattr(repo, "revenue_by_channel", None)
        if _fn is None:
            await _reply(update, "⚠️ Chức năng này chưa sẵn sàng. Thử lại sau.")
            return
        rows = _fn(gid, start, end, branch=None)
        if not rows:
            await _reply(update, fmt.no_data(f"kênh bán {label}"))
        else:
            await _reply(update, fmt.channels_block(label, rows))
    except Exception:  # noqa: BLE001
        log.exception("cmd_kenh | group=%s — lỗi", gid)
        await _reply(update, "⚠️ Không lấy được dữ liệu kênh bán. Thử lại sau nhé.")


async def cmd_tonkho(update, context) -> None:
    """Tồn kho mới nhất (tất cả cơ sở hoặc lọc theo tên hàng)."""
    gid = z.chat_id(update)
    try:
        arg = _arg(update).strip() or None  # arg tuỳ chọn: tên mặt hàng
        _fn = getattr(repo, "inventory_latest", None)
        if _fn is None:
            await _reply(update, "⚠️ Chức năng này chưa sẵn sàng. Thử lại sau.")
            return
        rows = _fn(gid, branch=None, name_like=arg)
        if not rows:
            await _reply(update, fmt.no_data("tồn kho"))
        else:
            await _reply(update, fmt.inventory_block(rows))
    except Exception:  # noqa: BLE001
        log.exception("cmd_tonkho | group=%s — lỗi", gid)
        await _reply(update, "⚠️ Không lấy được dữ liệu tồn kho. Thử lại sau nhé.")


async def cmd_coso(update, context) -> None:
    """Danh sách cơ sở đã có báo cáo."""
    gid = z.chat_id(update)
    try:
        _fn = getattr(repo, "list_branches", None)
        if _fn is None:
            await _reply(update, "⚠️ Chức năng này chưa sẵn sàng. Thử lại sau.")
            return
        # Không lọc start/end: lấy tất cả cơ sở kể cả báo cáo có report_date NULL
        rows = _fn(gid)
        if not rows:
            await _reply(update, fmt.no_data("cơ sở"))
        else:
            await _reply(update, fmt.branches_block(rows))
    except Exception:  # noqa: BLE001
        log.exception("cmd_coso | group=%s — lỗi", gid)
        await _reply(update, "⚠️ Không lấy được danh sách cơ sở. Thử lại sau nhé.")


# ── Hỏi tự do (tag @bot + câu hỏi) ─────────────────────────────────────────
_HELP_TRIGGERS = {"", "/start", "start", "/help", "help", "?", "hi", "hello"}

# Khi user @bot kèm lệnh gạch chéo → quy về ngôn ngữ tự nhiên cho router
_CMD_WORD_MAP = {
    "doanhthu": "doanh thu", "thongke": "thống kê", "baocao": "báo cáo",
    "donhang": "đơn hàng", "khach": "khách hàng", "top": "top",
    # Sprint 3
    "kenh": "doanh thu theo kênh", "tonkho": "tồn kho", "coso": "danh sách cơ sở",
}

# Cache tên bot (lấy 1 lần qua get_me) để bỏ khỏi câu hỏi khi bị @mention
_bot_names_cache: set[str] | None = None


async def _get_bot_names(context) -> set[str]:
    global _bot_names_cache
    if _bot_names_cache is None:
        names: set[str] = set()
        try:
            me = await context.bot.get_me()
            for attr in ("display_name", "account_name", "username"):
                v = getattr(me, attr, None)
                if v:
                    names.add(str(v).strip())
        except Exception:  # noqa: BLE001
            log.warning("get_me để lấy tên bot thất bại", exc_info=True)
        _bot_names_cache = names
    return _bot_names_cache


def _strip_mention(text: str, names: set[str]) -> str:
    """Bỏ '@Tên Bot' / '@bot' khỏi câu, giữ lại phần câu hỏi thật."""
    t = text
    for n in sorted(names, key=len, reverse=True):
        if n:
            t = re.sub(r"@?\s*" + re.escape(n), " ", t, flags=re.IGNORECASE)
    t = re.sub(r"@bot\b", " ", t, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", t).strip()


def _normalize_command(q: str) -> str:
    """'/doanhthu hôm nay' → 'doanh thu hôm nay' (khi user @bot kèm lệnh)."""
    first, sep, rest = q.partition(" ")
    key = first.lstrip("/").lower()
    if key in _CMD_WORD_MAP:
        return (_CMD_WORD_MAP[key] + (" " + rest if rest else "")).strip()
    return q


async def on_text(update, context) -> None:
    # [DIAG] Log thô để biết Zalo có đẩy tin chữ (không mention) cho bot không
    log.info(
        "RAW on_text | group=%s is_group=%s mentioned=%s text=%r",
        z.chat_id(update), z.is_group(update), z.is_bot_mentioned(update),
        z.message_text(update)[:60],
    )
    text = z.message_text(update)
    if not text:
        return
    if z.is_group(update) and not z.is_bot_mentioned(update):
        return
    gid = z.chat_id(update)
    uid, name = z.sender(update)
    q = _strip_mention(text, await _get_bot_names(context))
    log.info("on_text | group=%s sender=%s(%s) — câu hỏi: %.80s", gid, uid, name, q)

    # @bot trơn / @bot /start / @bot help → hiện hướng dẫn thân thiện
    if q.lower() in _HELP_TRIGGERS:
        await _reply(update, HELP)
        return

    q = _normalize_command(q)
    try:
        await _reply(update, await router.answer(q, gid))
    except Exception:  # noqa: BLE001
        log.exception("on_text | group=%s sender=%s — router.answer lỗi", gid, uid)
        await _reply(
            update,
            "⚠️ Mình chưa trả lời được lúc này. "
            "Thử lại sau hoặc hỏi kiểu \"@bot doanh thu hôm nay\" nhé.",
        )


# ── Helpers ───────────────────────────────────────────────────────────────────
def _arg(update) -> str:
    """Phần text sau lệnh, vd '/doanhthu tháng này' → 'tháng này'."""
    text = z.message_text(update)
    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ""
    return text


def _period_word(arg: str) -> str:
    """Trích period token từ phần text sau lệnh.

    Dùng chung bộ trích search-based của router để hiểu được cả cụm dài
    ('báo cáo ngày 19-7-2026'), ngày lẻ ('19/7'), 'tháng 3',
    'từ 1/7 đến 10/7'... thay vì fullmatch cả câu như trước.
    """
    a = arg.strip()
    if not a:
        return "today"
    from nlq.router import _keyword_period

    return _keyword_period(a.lower())

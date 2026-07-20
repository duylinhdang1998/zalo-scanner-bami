"""Lớp tương thích SDK Zalo Bot (python-zalo-bot).

Gom mọi thao tác chạm SDK vào 1 chỗ. Đã khớp với API thực tế:
- message.chat.id / chat.type
- message.from_user.display_name | account_name
- message.text  (ảnh: caption cũng nằm ở text nếu có)
- message.photo_url  → tải ảnh qua HTTP
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
from urllib.parse import urlparse

import httpx

log = logging.getLogger("bot.compat")

# ── SSRF protection ───────────────────────────────────────────────────────────
# Danh sách host/domain Zalo CDN được phép tải ảnh.
# Env ZALO_IMAGE_HOST_ALLOWLIST: chuỗi phân cách bằng dấu phẩy.
# Nếu rỗng ("") → cho phép tải bất kỳ host nào (dev mode) nhưng vẫn chặn IP nội bộ.
_raw_allowlist = os.getenv(
    "ZALO_IMAGE_HOST_ALLOWLIST",
    "zadn.vn,zaloapp.com,zalo.me,zdn.vn",
)
_HOST_ALLOWLIST: list[str] = [h.strip().lower() for h in _raw_allowlist.split(",") if h.strip()]


def _is_private_ip(hostname: str) -> bool:
    """Trả True nếu hostname resolve sang địa chỉ IP nội bộ (loopback/RFC-1918/link-local)."""
    try:
        addr = ipaddress.ip_address(hostname)
        return addr.is_loopback or addr.is_private or addr.is_link_local
    except ValueError:
        pass
    # hostname — resolve rồi kiểm tra
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except OSError:
        # Không resolve được → chặn để an toàn
        return True
    for _fam, _type, _proto, _canon, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
            if addr.is_loopback or addr.is_private or addr.is_link_local:
                return True
        except ValueError:
            continue
    return False


def _host_allowed(host: str) -> bool:
    """Kiểm tra host khớp allowlist (đuôi domain). Rỗng allowlist → cho phép."""
    host = host.lower()
    if not _HOST_ALLOWLIST:
        return True
    return any(host == allowed or host.endswith("." + allowed) for allowed in _HOST_ALLOWLIST)


def _check_redirect_url(location: str) -> None:
    """Kiểm tra URL redirect có an toàn không. Raise ValueError nếu bị chặn."""
    parsed = urlparse(location)
    rhost = parsed.hostname or ""
    if not _host_allowed(rhost):
        raise ValueError(
            f"Redirect bị chặn: host '{rhost}' không nằm trong danh sách cho phép. "
            "Liên hệ admin nếu đây là lỗi."
        )
    if _is_private_ip(rhost):
        raise ValueError(
            f"Redirect bị chặn: host '{rhost}' là địa chỉ nội bộ. "
            "Không cho phép tải ảnh từ mạng nội bộ."
        )

_MAX_IMAGE_BYTES = 15 * 1_024 * 1_024  # 15 MB
_CONNECT_TIMEOUT = 5.0   # giây
_READ_TIMEOUT = 30.0     # giây
_RETRY_COUNT = 3
_RETRY_BACKOFF = (1.0, 2.0)  # sleep giữa lần thử 1→2, 2→3


def chat_id(update) -> str | None:
    chat = getattr(getattr(update, "message", None), "chat", None)
    cid = getattr(chat, "id", None)
    return str(cid) if cid is not None else None


def is_group(update) -> bool:
    chat = getattr(getattr(update, "message", None), "chat", None)
    return "group" in str(getattr(chat, "type", "") or "").lower()


def sender(update) -> tuple[str | None, str | None]:
    u = getattr(getattr(update, "message", None), "from_user", None)
    if not u:
        return None, None
    uid = getattr(u, "id", None)
    name = getattr(u, "display_name", None) or getattr(u, "account_name", None)
    return (str(uid) if uid is not None else None), name


def message_text(update) -> str:
    msg = getattr(update, "message", None)
    return (getattr(msg, "text", None) or "").strip()


def photo_url(update) -> str | None:
    return getattr(getattr(update, "message", None), "photo_url", None)


def has_photo(update) -> bool:
    return bool(photo_url(update))


def is_sticker(update) -> bool:
    """Phát hiện sticker Zalo: kiểm tra sticker_id / type / thuộc tính sticker."""
    msg = getattr(update, "message", None)
    if msg is None:
        return False
    if getattr(msg, "sticker", None) is not None:
        return True
    if getattr(msg, "sticker_id", None) is not None:
        return True
    msg_type = str(getattr(msg, "type", "") or "").lower()
    if "sticker" in msg_type:
        return True
    return False


def is_bot_mentioned(update, bot_names: set[str] | None = None) -> bool:
    """Nhóm: coi là được gọi khi text chứa token '@bot' cụ thể hoặc khớp bot_names.

    Trước đây dùng heuristic rộng ("@" hoặc "bot") gây false-positive
    (ví dụ: "robot", "about", email). Chỉ khớp token '@bot' chính xác.
    """
    text = message_text(update).lower()
    if "@bot" in text:
        return True
    if bot_names and any(n in text for n in bot_names):
        return True
    return False


async def photo_bytes(update, context) -> tuple[bytes, str]:
    """Tải ảnh với retry x3 + backoff, timeout, giới hạn 15 MB, kiểm tra content-type.

    Bảo mật SSRF:
    - Không follow redirect tự động.
    - Khi nhận 3xx, kiểm tra host của Location trước khi follow.
    - Chặn host không khớp allowlist (ZALO_IMAGE_HOST_ALLOWLIST).
    - Chặn địa chỉ nội bộ (loopback/RFC-1918/link-local) sau khi resolve.
    - Nếu allowlist rỗng → cho phép mọi host nhưng vẫn chặn IP nội bộ.
    """
    url = photo_url(update)
    if not url:
        raise RuntimeError("Message không có photo_url.")

    # Kiểm tra host của URL gốc
    parsed_origin = urlparse(url)
    origin_host = parsed_origin.hostname or ""
    if not _host_allowed(origin_host):
        raise ValueError(
            f"Không được phép tải ảnh từ host '{origin_host}'. "
            "Host không nằm trong danh sách Zalo CDN cho phép."
        )
    if _is_private_ip(origin_host):
        raise ValueError(
            f"Không được phép tải ảnh: host '{origin_host}' là địa chỉ nội bộ."
        )

    timeout = httpx.Timeout(
        connect=_CONNECT_TIMEOUT,
        read=_READ_TIMEOUT,
        write=10.0,
        pool=5.0,
    )

    last_err: Exception = RuntimeError("Không thể kết nối tới máy chủ ảnh.")
    for attempt in range(_RETRY_COUNT):
        try:
            # follow_redirects=False — tự xử lý redirect để kiểm tra host đích
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as c:
                r = await c.get(url)

                # Xử lý redirect thủ công với kiểm tra SSRF
                redirect_count = 0
                while r.is_redirect and redirect_count < 5:
                    location = r.headers.get("location", "")
                    if not location:
                        break
                    _check_redirect_url(location)  # raises ValueError nếu bị chặn
                    log.debug("Redirect %d → %s", redirect_count + 1, location)
                    r = await c.get(location)
                    redirect_count += 1

                r.raise_for_status()

                ct = r.headers.get("content-type", "")
                ct_base = ct.split(";")[0].strip() if ct else ""
                if ct_base and not (ct_base.startswith("image/") or ct_base == "application/pdf"):
                    raise ValueError(
                        f"URL không trả về ảnh hoặc PDF (content-type: {ct!r}). "
                        "Có thể link đã hết hạn."
                    )

                # Kiểm tra kích thước qua header trước khi đọc hết body
                cl_header = r.headers.get("content-length")
                if cl_header and int(cl_header) > _MAX_IMAGE_BYTES:
                    size_mb = int(cl_header) // 1_048_576
                    raise ValueError(
                        f"Ảnh quá lớn ({size_mb} MB, tối đa 15 MB). "
                        "Nén ảnh rồi gửi lại nhé."
                    )

                data = r.content
                if len(data) > _MAX_IMAGE_BYTES:
                    size_mb = len(data) // 1_048_576
                    raise ValueError(
                        f"Ảnh quá lớn ({size_mb} MB, tối đa 15 MB). "
                        "Nén ảnh rồi gửi lại nhé."
                    )

                return data, _mime(url, ct or None)

        except ValueError:
            # Lỗi business (kích thước, content-type, SSRF) — không retry
            raise
        except httpx.HTTPStatusError as exc:
            last_err = exc
            log.warning(
                "Tải ảnh HTTP %s (lần %d/%d): %s",
                exc.response.status_code,
                attempt + 1,
                _RETRY_COUNT,
                exc,
            )
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
            last_err = exc
            log.warning(
                "Tải ảnh mạng lỗi (lần %d/%d): %s",
                attempt + 1,
                _RETRY_COUNT,
                type(exc).__name__,
            )
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            log.warning(
                "Tải ảnh lỗi không xác định (lần %d/%d): %s",
                attempt + 1,
                _RETRY_COUNT,
                exc,
            )

        if attempt < _RETRY_COUNT - 1:
            await asyncio.sleep(_RETRY_BACKOFF[attempt])

    raise RuntimeError(
        f"Tải ảnh thất bại sau {_RETRY_COUNT} lần thử: {last_err}"
    ) from last_err


def _mime(url: str, content_type: str | None) -> str:
    """Xác định MIME type từ content-type header hoặc extension URL.

    Hỗ trợ image/* và application/pdf (Vision API chấp nhận PDF).
    """
    if content_type:
        ct_base = content_type.split(";")[0].strip()
        if ct_base.startswith("image/") or ct_base == "application/pdf":
            return ct_base
    u = url.lower()
    if ".pdf" in u:
        return "application/pdf"
    if ".png" in u:
        return "image/png"
    if ".webp" in u:
        return "image/webp"
    return "image/jpeg"

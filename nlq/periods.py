"""Giải nghĩa 'khoảng thời gian' thành cặp (start, end).

Ngoài các token cũ (today, yesterday, …), resolve() còn hiểu:
  • "range:YYYY-MM-DD:YYYY-MM-DD" — khoảng ngày cụ thể (do _keyword_period tạo ra)
  • "tháng:M" hoặc "tháng:M:YYYY"  — tháng M năm nay (hoặc năm YYYY)
  • "quý:Q"   hoặc "quý:Q:YYYY"    — quý Q năm nay (hoặc năm YYYY)
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

Period = tuple[date, date]


def resolve(period: str | None) -> tuple[Period, str]:
    """Trả về ((start, end), nhãn tiếng Việt)."""
    today = date.today()
    p = (period or "today").strip()
    pl = p.lower()

    # ── Canonical tokens (không thay đổi) ─────────────────────────────
    if pl in ("today", "hom_nay", "hôm nay"):
        return (today, today), "hôm nay"
    if pl in ("yesterday", "hom_qua", "hôm qua"):
        y = today - timedelta(days=1)
        return (y, y), "hôm qua"
    if pl in ("this_week", "tuan_nay", "tuần này"):
        start = today - timedelta(days=today.weekday())
        return (start, today), "tuần này"
    if pl in ("last_week", "tuan_truoc"):
        start = today - timedelta(days=today.weekday() + 7)
        return (start, start + timedelta(days=6)), "tuần trước"
    if pl in ("this_month", "thang_nay", "tháng này"):
        return (today.replace(day=1), today), "tháng này"
    if pl in ("last_month", "thang_truoc", "tháng trước"):
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return (last_prev.replace(day=1), last_prev), "tháng trước"
    if pl in ("all", "tat_ca", "tất cả", "toàn bộ"):
        return (date(2000, 1, 1), date(2100, 1, 1)), "tất cả"

    # ── Structured token: range:YYYY-MM-DD:YYYY-MM-DD ─────────────────
    if pl.startswith("range:"):
        raw = p[6:]
        parts = raw.split(":", 1)
        if len(parts) == 2:
            try:
                start = date.fromisoformat(parts[0])
                end = date.fromisoformat(parts[1])
                if start <= end:
                    label = (
                        f"từ {start.strftime('%d/%m/%Y')} đến {end.strftime('%d/%m/%Y')}"
                    )
                    return (start, end), label
            except ValueError:
                pass

    # ── Structured token: tháng:M[:YYYY] ──────────────────────────────
    m = re.fullmatch(r"t(?:háng|hang):(\d{1,2})(?::(\d{4}))?", pl)
    if m:
        month = int(m.group(1))
        year = int(m.group(2)) if m.group(2) else today.year
        if 1 <= month <= 12:
            start, end = month_bounds(year, month)
            return (start, end), f"tháng {month}/{year}"

    # ── Structured token: quý:Q[:YYYY] ────────────────────────────────
    m = re.fullmatch(r"(?:quý|quy):(\d)(?::(\d{4}))?", pl)
    if m:
        q = int(m.group(1))
        year = int(m.group(2)) if m.group(2) else today.year
        if 1 <= q <= 4:
            start_month = (q - 1) * 3 + 1
            end_month = q * 3
            start = date(year, start_month, 1)
            _, last_day = calendar.monthrange(year, end_month)
            end = date(year, end_month, last_day)
            return (start, end), f"quý {q}/{year}"

    # ── Structured token: day:YYYY-MM-DD ──────────────────────────────
    if pl.startswith("day:"):
        try:
            d = date.fromisoformat(pl[4:])
            return (d, d), f"ngày {d.strftime('%d/%m/%Y')}"
        except ValueError:
            pass

    # ── Ngày lẻ trực tiếp: "19/07", "19-07-2026", "ngày 19.07" ───────
    m = re.fullmatch(
        r"(?:ngày\s+|ngay\s+)?(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2,4}))?", pl
    )
    if m:
        try:
            day_ = int(m.group(1))
            month_ = int(m.group(2))
            year_ = int(m.group(3)) if m.group(3) else today.year
            if year_ < 100:
                year_ += 2000
            d = date(year_, month_, day_)
            return (d, d), f"ngày {d.strftime('%d/%m/%Y')}"
        except ValueError:
            pass

    # Mặc định: hôm nay
    return (today, today), "hôm nay"


def month_bounds(year: int, month: int) -> Period:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)

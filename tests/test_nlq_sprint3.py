"""Tests for Sprint 3 NLQ router + bot command handlers.

Covers:
- Period parsing: "19/07", "19/07/2026", "ngày 19/07", "day:YYYY-MM-DD" tokens
- _extract_branch: "cơ sở X" → "Cơ sở X", "chi nhánh X" → "X"
- _keyword_route: channels/financials/inventory/branches intents
- answer() dispatch: channels, financials, inventory, branches (with + without data)
- cmd_kenh, cmd_tonkho, cmd_coso handlers
- /baocao (report intent) merges store+sale data
"""
from __future__ import annotations

from datetime import date

import pytest

import bot.handlers as handlers
import db.repository
from tests.conftest import FakeContext, FakeUpdate


# ── Seed helper ───────────────────────────────────────────────────────────────

def _sr_data(
    report_date: str = "2026-07-01",
    branch: str | None = "Cơ sở A",
    gross: int = 5_000_000,
    cost: int = 1_000_000,
    net: int = 4_000_000,
    cash: int = 3_000_000,
    transfer: int = 1_000_000,
    channels: list | None = None,
    products: list | None = None,
    inventory: list | None = None,
) -> dict:
    if channels is None:
        channels = [
            {"channel": "cua_hang", "revenue": 3_000_000, "banh_qty": 10.0, "nuoc_qty": 5.0},
            {"channel": "grab", "revenue": 2_000_000, "banh_qty": 4.0, "nuoc_qty": 2.0},
        ]
    if products is None:
        products = [
            {"name": "Bánh mì", "category": "banh",
             "grab": 5.0, "now_shopee": 0.0, "xanh": 0.0, "be": 0.0, "cua_hang": 8.0, "total": 13.0},
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
                "gross_revenue": gross, "cost": cost, "net_revenue": net,
                "cash": cash, "transfer": transfer, "discrepancy": 0,
            },
            "channels": channels,
            "products": products,
            "inventory": inventory,
        },
    }


def _seed(
    group_id: str = "g1",
    image_hash: str = "hSeed",
    report_date: str | None = None,
    **kwargs,
):
    return db.repository.save_store_report(
        group_id=group_id,
        sender_id="u1",
        sender_name="Alice",
        image_url=None,
        image_hash=image_hash,
        data=_sr_data(report_date=report_date or date.today().isoformat(), **kwargs),
    )


# ════════════════════════════════════════════════════════════════════════════
# 1. Period parsing — resolve() handles structured tokens
# ════════════════════════════════════════════════════════════════════════════

class TestPeriodParsing:

    def test_resolve_day_token(self):
        from nlq.periods import resolve
        (start, end), label = resolve("day:2026-07-19")
        assert start == date(2026, 7, 19)
        assert end == date(2026, 7, 19)
        assert "19/07/2026" in label

    def test_resolve_dd_mm_slash(self):
        from nlq.periods import resolve
        (start, end), label = resolve("19/07")
        assert start.month == 7
        assert start.day == 19
        assert start == end

    def test_resolve_dd_mm_yyyy_slash(self):
        from nlq.periods import resolve
        (start, end), label = resolve("19/07/2026")
        assert start == date(2026, 7, 19)
        assert end == date(2026, 7, 19)

    def test_resolve_ngay_prefix(self):
        from nlq.periods import resolve
        (start, end), label = resolve("ngày 19/07")
        assert start.month == 7
        assert start.day == 19
        assert start == end

    def test_resolve_ngay_lowercase(self):
        from nlq.periods import resolve
        (start, end), label = resolve("ngay 19/07")
        assert start.day == 19
        assert start.month == 7

    def test_resolve_dd_mm_two_digit_year(self):
        from nlq.periods import resolve
        (start, end), label = resolve("19/07/26")
        # two-digit year → +2000
        assert start == date(2026, 7, 19)

    def test_keyword_period_produces_day_token_from_ngay(self):
        from nlq.router import _keyword_period
        token = _keyword_period("doanh thu ngày 19/07")
        assert token.startswith("day:")
        assert "-07-19" in token

    def test_keyword_period_dd_mm_yyyy(self):
        from nlq.router import _keyword_period
        token = _keyword_period("báo cáo ngày 19/07/2026")
        assert token == "day:2026-07-19"

    def test_keyword_period_bare_date(self):
        from nlq.router import _keyword_period
        # bare "19/07" in text without "ngày"
        token = _keyword_period("tồn kho 19/07")
        assert token.startswith("day:")
        assert "-07-19" in token

    def test_day_token_round_trip(self):
        """_keyword_period → resolve produces correct single-day range."""
        from nlq.router import _keyword_period
        from nlq.periods import resolve
        token = _keyword_period("doanh thu ngày 05/03/2026")
        (start, end), label = resolve(token)
        assert start == date(2026, 3, 5)
        assert end == date(2026, 3, 5)


# ════════════════════════════════════════════════════════════════════════════
# 2. _extract_branch
# ════════════════════════════════════════════════════════════════════════════

class TestExtractBranch:

    def test_co_so_prefix_preserved(self):
        from nlq.router import _extract_branch
        result = _extract_branch("doanh thu cơ sở Quận 1 hôm nay")
        assert result is not None
        assert result.startswith("Cơ sở")
        assert "Quận 1" in result

    def test_co_so_ascii_variant(self):
        from nlq.router import _extract_branch
        result = _extract_branch("co so A tháng này")
        assert result is not None
        assert "A" in result

    def test_chi_nhanh_no_prefix(self):
        from nlq.router import _extract_branch
        result = _extract_branch("tồn kho chi nhánh Đống Đa hôm nay")
        assert result is not None
        assert not result.startswith("Cơ sở")
        assert "Đống Đa" in result

    def test_chi_nhanh_ascii(self):
        from nlq.router import _extract_branch
        result = _extract_branch("chi nhanh Bắc Ninh hôm nay")
        assert result is not None
        assert "Bắc Ninh" in result

    def test_no_branch_keyword_returns_none(self):
        from nlq.router import _extract_branch
        result = _extract_branch("doanh thu hôm nay tổng hợp")
        assert result is None

    def test_branch_truncated_at_50(self):
        from nlq.router import _extract_branch
        long_q = "cơ sở " + "x" * 100
        result = _extract_branch(long_q)
        assert result is not None
        assert len(result) <= 50


# ════════════════════════════════════════════════════════════════════════════
# 3. _keyword_route → Sprint 3 intents
# ════════════════════════════════════════════════════════════════════════════

class TestKeywordRoute:

    def test_ton_kho_routes_inventory(self):
        from nlq.router import _keyword_route
        intent, _p, _l = _keyword_route("tồn kho hiện tại")
        assert intent == "inventory"

    def test_ton_hang_routes_inventory(self):
        from nlq.router import _keyword_route
        # "tồn hàng" without "còn bao nhiêu" → inventory (not product)
        intent, _p, _l = _keyword_route("tồn hàng hiện tại bao nhiêu")
        assert intent == "inventory"

    def test_theo_kenh_routes_channels(self):
        from nlq.router import _keyword_route
        intent, _p, _l = _keyword_route("doanh thu theo kênh hôm nay")
        assert intent == "channels"

    def test_kenh_ban_routes_channels(self):
        from nlq.router import _keyword_route
        intent, _p, _l = _keyword_route("kênh bán hàng nào cao nhất")
        assert intent == "channels"

    def test_tai_chinh_routes_financials(self):
        from nlq.router import _keyword_route
        intent, _p, _l = _keyword_route("tài chính tháng này")
        assert intent == "financials"

    def test_tien_mat_routes_financials(self):
        from nlq.router import _keyword_route
        intent, _p, _l = _keyword_route("tiền mặt hôm nay bao nhiêu")
        assert intent == "financials"

    def test_chuyen_khoan_routes_financials(self):
        from nlq.router import _keyword_route
        intent, _p, _l = _keyword_route("chuyển khoản tháng này")
        assert intent == "financials"

    def test_net_routes_financials(self):
        from nlq.router import _keyword_route
        intent, _p, _l = _keyword_route("xem net hôm nay")
        assert intent == "financials"

    def test_danh_sach_co_so_routes_branches(self):
        from nlq.router import _keyword_route
        intent, _p, _l = _keyword_route("danh sách cơ sở")
        assert intent == "branches"

    def test_cac_co_so_routes_branches(self):
        from nlq.router import _keyword_route
        intent, _p, _l = _keyword_route("các cơ sở đang hoạt động")
        assert intent == "branches"


# ════════════════════════════════════════════════════════════════════════════
# 4. answer() — Sprint 3 intent dispatch
# ════════════════════════════════════════════════════════════════════════════

class TestAnswerChannels:

    async def test_channels_with_data_returns_channel_info(self, fresh_db):
        _seed(group_id="g1", image_hash="hC1")
        from nlq.router import answer
        reply = await answer("doanh thu theo kênh hôm nay", "g1")
        # Should mention channels or revenue
        assert any(kw in reply for kw in ("kênh", "Doanh thu", "Grab", "Cửa hàng", "đ"))

    async def test_channels_empty_returns_honest_no_data(self, fresh_db):
        from nlq.router import answer
        reply = await answer("doanh thu theo kênh hôm nay", "g1")
        assert "Chưa" in reply or "chưa" in reply

    async def test_channels_with_branch_filter(self, fresh_db):
        _seed(group_id="g1", image_hash="hC2", branch="Cơ sở Quận 1")
        _seed(group_id="g1", image_hash="hC3", branch="Cơ sở Quận 2",
              channels=[{"channel": "now_shopee", "revenue": 500_000,
                         "banh_qty": 2.0, "nuoc_qty": 1.0}])
        from nlq.router import answer
        reply = await answer("doanh thu theo kênh cơ sở Quận 1 hôm nay", "g1")
        # Reply should be about channels (not an error)
        assert "đ" in reply or "kênh" in reply.lower() or "Chưa" in reply


class TestAnswerFinancials:

    async def test_financials_with_data(self, fresh_db):
        _seed(group_id="g1", image_hash="hF1",
              gross=5_000_000, cost=1_000_000, net=4_000_000)
        from nlq.router import answer
        reply = await answer("tài chính hôm nay", "g1")
        assert any(kw in reply for kw in ("Tài chính", "tài chính", "Doanh thu", "Net", "đ"))

    async def test_financials_empty_returns_honest(self, fresh_db):
        from nlq.router import answer
        reply = await answer("tài chính hôm nay", "g1")
        assert "Chưa" in reply or "chưa" in reply

    async def test_financials_numbers_match_seed(self, fresh_db):
        _seed(group_id="g1", image_hash="hF2",
              gross=7_000_000, net=5_000_000)
        from nlq.router import answer
        reply = await answer("tài chính hôm nay", "g1")
        # 7.000.000đ or 7,000,000đ style
        assert "7.000.000đ" in reply or "7000000" in reply


class TestAnswerInventory:

    async def test_inventory_with_data(self, fresh_db):
        _seed(group_id="g1", image_hash="hI1")
        from nlq.router import answer
        reply = await answer("tồn kho hiện tại", "g1")
        assert any(kw in reply for kw in ("Tồn kho", "tồn", "Bột mì"))

    async def test_inventory_empty_returns_honest(self, fresh_db):
        from nlq.router import answer
        reply = await answer("tồn kho hiện tại", "g1")
        assert "Chưa" in reply or "chưa" in reply

    async def test_inventory_close_qty_shown(self, fresh_db):
        inv = [{"name": "Bột lúa mì", "open": 10.0, "import": 2.0, "discard": 0.0, "close": 12.0}]
        _seed(group_id="g1", image_hash="hI2", inventory=inv)
        from nlq.router import answer
        reply = await answer("tồn kho hiện tại", "g1")
        assert "12" in reply


class TestAnswerBranches:

    async def test_branches_with_data(self, fresh_db):
        _seed(group_id="g1", image_hash="hBr1", branch="Cơ sở A",
              report_date="2026-07-01")
        _seed(group_id="g1", image_hash="hBr2", branch="Cơ sở B",
              report_date="2026-07-02")
        from nlq.router import answer
        reply = await answer("danh sách cơ sở", "g1")
        assert any(b in reply for b in ("Cơ sở A", "Cơ sở B", "cơ sở"))

    async def test_branches_empty_returns_honest(self, fresh_db):
        from nlq.router import answer
        reply = await answer("danh sách cơ sở", "g1")
        assert "Chưa" in reply or "chưa" in reply


# ════════════════════════════════════════════════════════════════════════════
# 5. /baocao — report intent merges store+sale
# ════════════════════════════════════════════════════════════════════════════

class TestBaocaoMerge:

    async def test_baocao_with_store_data_shows_channels_or_financials(self, fresh_db):
        _seed(group_id="g1", image_hash="hBao1")
        from nlq.router import answer
        reply = await answer("báo cáo hôm nay", "g1")
        # Should include channel or financial data from store_report
        has_store = any(kw in reply for kw in (
            "kênh", "Doanh thu", "Tài chính", "tài chính", "đ"
        ))
        assert has_store, f"Expected store_report data in reply, got: {reply!r}"

    async def test_baocao_empty_db_returns_no_data_message(self, fresh_db):
        from nlq.router import answer
        reply = await answer("báo cáo hôm nay", "g1")
        assert "Chưa" in reply or "chưa" in reply or "không" in reply.lower()


# ════════════════════════════════════════════════════════════════════════════
# 6. cmd_kenh
# ════════════════════════════════════════════════════════════════════════════

class TestCmdKenh:

    async def test_no_data_replies_no_data(self, fresh_db):
        update = FakeUpdate(text="/kenh hôm nay", is_group=True, cid="g1")
        await handlers.cmd_kenh(update, FakeContext())
        assert len(update.replies) > 0
        reply = update.replies[0]
        assert "Chưa" in reply or "chưa" in reply

    async def test_with_data_replies_channel_info(self, fresh_db):
        _seed(group_id="g1", image_hash="hCK1")
        update = FakeUpdate(text="/kenh hôm nay", is_group=True, cid="g1")
        await handlers.cmd_kenh(update, FakeContext())
        assert len(update.replies) > 0
        reply = update.replies[0]
        assert any(kw in reply for kw in ("kênh", "Doanh thu", "đ", "Grab", "Cửa hàng"))

    async def test_no_arg_defaults_without_crash(self, fresh_db):
        update = FakeUpdate(text="/kenh", is_group=True, cid="g1")
        await handlers.cmd_kenh(update, FakeContext())
        assert len(update.replies) == 1  # always replies once

    async def test_tuan_nay_arg(self, fresh_db):
        _seed(group_id="g1", image_hash="hCK2",
              report_date=date.today().isoformat())
        update = FakeUpdate(text="/kenh tuần này", is_group=True, cid="g1")
        await handlers.cmd_kenh(update, FakeContext())
        assert len(update.replies) > 0


# ════════════════════════════════════════════════════════════════════════════
# 7. cmd_tonkho
# ════════════════════════════════════════════════════════════════════════════

class TestCmdTonKho:

    async def test_no_data_replies_no_data(self, fresh_db):
        update = FakeUpdate(text="/tonkho", is_group=True, cid="g1")
        await handlers.cmd_tonkho(update, FakeContext())
        assert len(update.replies) > 0
        assert "Chưa" in update.replies[0] or "chưa" in update.replies[0]

    async def test_with_data_replies_inventory(self, fresh_db):
        _seed(group_id="g1", image_hash="hTK1")
        update = FakeUpdate(text="/tonkho", is_group=True, cid="g1")
        await handlers.cmd_tonkho(update, FakeContext())
        assert len(update.replies) > 0
        reply = update.replies[0]
        assert any(kw in reply for kw in ("tồn", "Bột mì", "Tồn kho"))

    async def test_name_filter_arg_applied(self, fresh_db):
        inv = [
            {"name": "Bột mì", "open": 10.0, "import": 0.0, "discard": 0.0, "close": 10.0},
            {"name": "Đường", "open": 5.0, "import": 0.0, "discard": 0.0, "close": 5.0},
        ]
        _seed(group_id="g1", image_hash="hTK2", inventory=inv)
        update = FakeUpdate(text="/tonkho Bột mì", is_group=True, cid="g1")
        await handlers.cmd_tonkho(update, FakeContext())
        reply = update.replies[0]
        assert "Bột mì" in reply
        assert "Đường" not in reply

    async def test_nonexistent_name_filter_returns_no_data(self, fresh_db):
        _seed(group_id="g1", image_hash="hTK3")
        update = FakeUpdate(text="/tonkho XYZKhongTonTai", is_group=True, cid="g1")
        await handlers.cmd_tonkho(update, FakeContext())
        assert "Chưa" in update.replies[0] or "chưa" in update.replies[0]


# ════════════════════════════════════════════════════════════════════════════
# 8. cmd_coso
# ════════════════════════════════════════════════════════════════════════════

class TestCmdCoso:

    async def test_no_data_replies_no_data(self, fresh_db):
        update = FakeUpdate(text="/coso", is_group=True, cid="g1")
        await handlers.cmd_coso(update, FakeContext())
        assert len(update.replies) > 0
        assert "Chưa" in update.replies[0] or "chưa" in update.replies[0]

    async def test_with_data_replies_branch_list(self, fresh_db):
        db.repository.save_store_report(
            group_id="g1", sender_id="u1", sender_name="Alice",
            image_url=None, image_hash="hCS1",
            data=_sr_data(report_date="2026-07-01", branch="Cơ sở A"),
        )
        update = FakeUpdate(text="/coso", is_group=True, cid="g1")
        await handlers.cmd_coso(update, FakeContext())
        assert len(update.replies) > 0
        reply = update.replies[0]
        assert "Cơ sở A" in reply

    async def test_multiple_branches_all_listed(self, fresh_db):
        db.repository.save_store_report(
            group_id="g1", sender_id="u1", sender_name="Alice",
            image_url=None, image_hash="hCS2",
            data=_sr_data(report_date="2026-07-01", branch="Cơ sở A"),
        )
        db.repository.save_store_report(
            group_id="g1", sender_id="u1", sender_name="Alice",
            image_url=None, image_hash="hCS3",
            data=_sr_data(report_date="2026-07-02", branch="Cơ sở B"),
        )
        update = FakeUpdate(text="/coso", is_group=True, cid="g1")
        await handlers.cmd_coso(update, FakeContext())
        reply = update.replies[0]
        assert "Cơ sở A" in reply
        assert "Cơ sở B" in reply

    async def test_coso_uses_all_time_range(self, fresh_db):
        """cmd_coso uses resolve('all') — should show branches from any date."""
        db.repository.save_store_report(
            group_id="g1", sender_id="u1", sender_name="Alice",
            image_url=None, image_hash="hCS4",
            data=_sr_data(report_date="2020-01-01", branch="Cơ sở Cũ"),
        )
        update = FakeUpdate(text="/coso", is_group=True, cid="g1")
        await handlers.cmd_coso(update, FakeContext())
        assert "Cơ sở Cũ" in update.replies[0]

"""Tests for nlq/periods.py — resolve() for all period strings."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from nlq.periods import resolve, month_bounds


class TestResolve:
    def test_today(self):
        (start, end), label = resolve("today")
        today = date.today()
        assert start == today
        assert end == today
        assert "hôm nay" in label

    def test_today_vietnamese(self):
        (start, end), _ = resolve("hôm nay")
        today = date.today()
        assert start == today

    def test_yesterday(self):
        (start, end), label = resolve("yesterday")
        yesterday = date.today() - timedelta(days=1)
        assert start == yesterday
        assert end == yesterday
        assert "hôm qua" in label

    def test_yesterday_no_diacritic(self):
        (start, end), _ = resolve("hom_qua")
        yesterday = date.today() - timedelta(days=1)
        assert start == yesterday

    def test_this_week(self):
        (start, end), label = resolve("this_week")
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        assert start == week_start
        assert end == today
        assert "tuần này" in label

    def test_this_week_vietnamese(self):
        (start, end), _ = resolve("tuần này")
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        assert start == week_start

    def test_last_week(self):
        (start, end), label = resolve("last_week")
        today = date.today()
        week_start = today - timedelta(days=today.weekday() + 7)
        assert start == week_start
        assert end == week_start + timedelta(days=6)
        assert "tuần trước" in label

    def test_this_month(self):
        (start, end), label = resolve("this_month")
        today = date.today()
        assert start == today.replace(day=1)
        assert end == today
        assert "tháng này" in label

    def test_this_month_vietnamese(self):
        (start, end), _ = resolve("tháng này")
        today = date.today()
        assert start == today.replace(day=1)

    def test_last_month(self):
        (start, end), label = resolve("last_month")
        today = date.today()
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        expected_start = last_prev.replace(day=1)
        assert start == expected_start
        assert end == last_prev
        assert "tháng trước" in label

    def test_last_month_no_diacritic(self):
        (start, end), _ = resolve("thang_truoc")
        today = date.today()
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        assert end == last_prev

    def test_all(self):
        (start, end), label = resolve("all")
        assert start.year <= 2000
        assert end.year >= 2100
        assert "tất cả" in label

    def test_all_aliases(self):
        for alias in ("tat_ca", "tất cả", "toàn bộ"):
            (start, end), _ = resolve(alias)
            assert start.year <= 2000

    def test_none_defaults_to_today(self):
        (start, end), label = resolve(None)
        today = date.today()
        assert start == today
        assert end == today

    def test_unknown_defaults_to_today(self):
        (start, end), label = resolve("không_biết")
        today = date.today()
        assert start == today


class TestMonthBounds:
    def test_january_2024(self):
        start, end = month_bounds(2024, 1)
        assert start == date(2024, 1, 1)
        assert end == date(2024, 1, 31)

    def test_february_leap_year(self):
        start, end = month_bounds(2024, 2)
        assert start == date(2024, 2, 1)
        assert end == date(2024, 2, 29)

    def test_february_non_leap(self):
        start, end = month_bounds(2023, 2)
        assert end == date(2023, 2, 28)

    def test_december(self):
        start, end = month_bounds(2024, 12)
        assert start == date(2024, 12, 1)
        assert end == date(2024, 12, 31)

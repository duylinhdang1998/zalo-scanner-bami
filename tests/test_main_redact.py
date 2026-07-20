"""Tests for main._redact_url() — password masking in log URLs."""
from __future__ import annotations

import pytest

from main import _redact_url


class TestRedactUrl:
    def test_sqlite_url_unchanged(self):
        url = "sqlite:///data/scanner.db"
        assert _redact_url(url) == url

    def test_sqlite_memory_unchanged(self):
        url = "sqlite:///:memory:"
        assert _redact_url(url) == url

    def test_postgres_password_redacted(self):
        url = "postgresql+psycopg://user:secret@db.example.com:5432/mydb"
        result = _redact_url(url)
        assert "secret" not in result
        assert "*:*@" in result

    def test_postgres_host_preserved(self):
        url = "postgresql+psycopg://user:pass@db.supabase.co:5432/postgres"
        result = _redact_url(url)
        assert "db.supabase.co" in result

    def test_postgres_port_preserved(self):
        url = "postgresql+psycopg://user:pass@host:5432/db"
        result = _redact_url(url)
        assert "5432" in result

    def test_postgres_no_port(self):
        url = "postgresql://user:pass@host/db"
        result = _redact_url(url)
        assert "secret" not in result
        assert "*:*@" in result

    def test_url_without_credentials_unchanged(self):
        url = "postgresql://db.example.com:5432/mydb"
        assert _redact_url(url) == url

    def test_url_with_only_username_redacted(self):
        # URL with username but no explicit password
        url = "postgresql://username@host/db"
        result = _redact_url(url)
        # Should still be handled (username present → redact)
        assert "username" not in result or "*" in result

    def test_redacted_url_is_valid_string(self):
        url = "postgresql://u:p@host:5432/db"
        result = _redact_url(url)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_special_chars_in_password_redacted(self):
        url = "postgresql://user:p%40ssw0rd!@host:5432/db"
        result = _redact_url(url)
        assert "p%40ssw0rd" not in result
        assert "*:*@" in result

    def test_supabase_pooled_url_redacted(self):
        """Supabase pooled connection URL pattern."""
        url = "postgresql+psycopg://postgres.abc:mysecret@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres"
        result = _redact_url(url)
        assert "mysecret" not in result
        assert "pooler.supabase.com" in result

    def test_returns_parseable_url_prefix(self):
        """Redacted URL should keep the scheme intact."""
        url = "postgresql+psycopg://user:secret@host:5432/db"
        result = _redact_url(url)
        assert result.startswith("postgresql+psycopg://")

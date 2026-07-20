"""Thêm 4 bảng báo cáo cửa hàng theo ngày: store_reports, report_channels,
report_products, report_inventory — Sprint 3.

Revision ID: 003
Revises: 002
Create Date: 2026-07-20

Tiền lưu BigInteger (VND sau khi vision đã ×1000 từ sheet nghìn đồng).
scan_id FK dùng ondelete="SET NULL" để giữ báo cáo khi Scan bị xoá.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── store_reports ─────────────────────────────────────────────────────────
    op.create_table(
        "store_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scan_id", sa.Integer(), sa.ForeignKey("scans.id", ondelete="SET NULL"), nullable=True),
        sa.Column("group_id", sa.String(64), nullable=True),
        sa.Column("report_date", sa.Date(), nullable=True),
        sa.Column("branch", sa.String(128), nullable=True),
        sa.Column("image_hash", sa.String(64), nullable=True),
        # Tài chính (VND)
        sa.Column("gross_revenue", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cost", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("net_revenue", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cash", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("transfer", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("discrepancy", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        # Dedup: cùng group_id + image_hash
        sa.UniqueConstraint("group_id", "image_hash", name="uq_store_reports_group_hash"),
    )
    op.create_index("ix_store_reports_group_id", "store_reports", ["group_id"])
    op.create_index("ix_store_reports_report_date", "store_reports", ["report_date"])
    op.create_index("ix_store_reports_branch", "store_reports", ["branch"])

    # ── report_channels ───────────────────────────────────────────────────────
    op.create_table(
        "report_channels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "report_id",
            sa.Integer(),
            sa.ForeignKey("store_reports.id"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("revenue", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("banh_qty", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("nuoc_qty", sa.Float(), nullable=False, server_default="0.0"),
    )
    op.create_index("ix_report_channels_report_id", "report_channels", ["report_id"])

    # ── report_products ───────────────────────────────────────────────────────
    op.create_table(
        "report_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "report_id",
            sa.Integer(),
            sa.ForeignKey("store_reports.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(32), nullable=True),
        sa.Column("qty_grab", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("qty_now_shopee", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("qty_xanh", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("qty_be", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("qty_cua_hang", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("qty_total", sa.Float(), nullable=False, server_default="0.0"),
    )
    op.create_index("ix_report_products_report_id", "report_products", ["report_id"])
    op.create_index("ix_report_products_name", "report_products", ["name"])

    # ── report_inventory ──────────────────────────────────────────────────────
    op.create_table(
        "report_inventory",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "report_id",
            sa.Integer(),
            sa.ForeignKey("store_reports.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("open_qty", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("import_qty", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("discard_qty", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("close_qty", sa.Float(), nullable=False, server_default="0.0"),
    )
    op.create_index("ix_report_inventory_report_id", "report_inventory", ["report_id"])
    op.create_index("ix_report_inventory_name", "report_inventory", ["name"])


def downgrade() -> None:
    op.drop_table("report_inventory")
    op.drop_table("report_products")
    op.drop_table("report_channels")
    op.drop_table("store_reports")

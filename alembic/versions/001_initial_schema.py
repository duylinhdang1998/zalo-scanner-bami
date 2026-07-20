"""Initial schema — 3 bảng: scans, documents, line_items (trạng thái Phase 1).

Revision ID: 001
Revises:
Create Date: 2026-07-19
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "001"
down_revision: str | None = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.String(64), nullable=True),
        sa.Column("sender_id", sa.String(64), nullable=True),
        sa.Column("sender_name", sa.String(128), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("raw_json", sa.Text(), nullable=True),
        sa.Column("doc_type", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_scans_group_id", "scans", ["group_id"])

    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scan_id", sa.Integer(), sa.ForeignKey("scans.id"), nullable=True),
        sa.Column("group_id", sa.String(64), nullable=True),
        sa.Column("doc_type", sa.String(16), nullable=False),
        sa.Column("doc_date", sa.Date(), nullable=True),
        sa.Column("party_name", sa.String(255), nullable=True),
        sa.Column("total_amount", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("currency", sa.String(8), nullable=False, server_default="VND"),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("tracking_code", sa.String(64), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_documents_group_id", "documents", ["group_id"])
    op.create_index("ix_documents_doc_type", "documents", ["doc_type"])
    op.create_index("ix_documents_doc_date", "documents", ["doc_date"])

    op.create_table(
        "line_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("product_name", sa.String(255), nullable=True),
        sa.Column("sku", sa.String(64), nullable=True),
        sa.Column("quantity", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("unit_price", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("amount", sa.Float(), nullable=False, server_default="0.0"),
    )
    op.create_index("ix_line_items_document_id", "line_items", ["document_id"])
    op.create_index("ix_line_items_product_name", "line_items", ["product_name"])
    op.create_index("ix_line_items_sku", "line_items", ["sku"])


def downgrade() -> None:
    op.drop_table("line_items")
    op.drop_table("documents")
    op.drop_table("scans")

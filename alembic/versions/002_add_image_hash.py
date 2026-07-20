"""Thêm cột image_hash vào scans + unique constraint (group_id, image_hash).

Revision ID: 002
Revises: 001
Create Date: 2026-07-19

image_hash: sha256 hex (64 chars) của bytes ảnh gốc.
Logic dedup trong db/repository.py: nếu cùng group_id + image_hash đã tồn tại,
save_extraction() trả Document cũ mà không tạo bản ghi mới.

Dùng UNIQUE constraint thay vì plain index để DB enforce tính duy nhất,
bắt race condition INSERT đồng thời bằng IntegrityError.
NULL hash (ảnh không hash được) KHÔNG bị ràng buộc — cả Postgres lẫn SQLite
đều coi NULL là distinct trong UNIQUE, nên các scan không có hash không xung đột.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # batch_alter_table: SQLite-compatible copy-and-move strategy.
    # Postgres ignores the overhead; SQLite requires it for ADD CONSTRAINT.
    with op.batch_alter_table("scans") as batch_op:
        batch_op.add_column(sa.Column("image_hash", sa.String(64), nullable=True))
        batch_op.create_unique_constraint("uq_scans_group_hash", ["group_id", "image_hash"])


def downgrade() -> None:
    with op.batch_alter_table("scans") as batch_op:
        batch_op.drop_constraint("uq_scans_group_hash", type_="unique")
        batch_op.drop_column("image_hash")

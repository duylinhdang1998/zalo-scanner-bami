"""Mô hình dữ liệu thống nhất cho 2 loại chứng từ: bán hàng (sale) + đơn/vận đơn (order),
và báo cáo cửa hàng theo ngày (store_report) — Sprint 3."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, DateTime, Date, Float, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Scan(Base):
    """Mỗi lần quét 1 ảnh — lưu vết + JSON thô để audit/khôi phục.

    image_hash: sha256 hex của bytes ảnh gốc — dùng để phát hiện ảnh trùng
                cùng group_id + image_hash → bỏ qua, trả Document cũ.
    """

    __tablename__ = "scans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[str | None] = mapped_column(String(64), index=True)
    sender_id: Mapped[str | None] = mapped_column(String(64))
    sender_name: Mapped[str | None] = mapped_column(String(128))
    image_url: Mapped[str | None] = mapped_column(Text)
    image_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)  # sha256 hex (64 chars)
    raw_json: Mapped[str | None] = mapped_column(Text)          # JSON model trả về
    doc_type: Mapped[str] = mapped_column(String(16), default="unknown")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|confirmed|rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))

    document: Mapped["Document"] = relationship(back_populates="scan", uselist=False)

    __table_args__ = (
        # Unique constraint: cùng group + hash chỉ lưu 1 lần.
        # NULL hash (ảnh không hash được) KHÔNG bị ràng buộc —
        # cả Postgres lẫn SQLite đều coi NULL là distinct trong UNIQUE.
        UniqueConstraint("group_id", "image_hash", name="uq_scans_group_hash"),
    )


class Document(Base):
    """Bản ghi nghiệp vụ đã chuẩn hoá (1 hoá đơn hoặc 1 đơn hàng)."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_id: Mapped[int | None] = mapped_column(ForeignKey("scans.id"))
    group_id: Mapped[str | None] = mapped_column(String(64), index=True)

    doc_type: Mapped[str] = mapped_column(String(16), index=True)   # sale | order
    doc_date: Mapped[date | None] = mapped_column(Date, index=True)
    party_name: Mapped[str | None] = mapped_column(String(255))     # khách hàng / người nhận
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(8), default="VND")
    status: Mapped[str | None] = mapped_column(String(32))          # đơn: cho_giao|da_giao|huy...
    tracking_code: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)

    created_by: Mapped[str | None] = mapped_column(String(128))     # người gửi ảnh
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))

    scan: Mapped["Scan"] = relationship(back_populates="document")
    items: Mapped[list["LineItem"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class LineItem(Base):
    """Dòng sản phẩm trong 1 chứng từ."""

    __tablename__ = "line_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    product_name: Mapped[str | None] = mapped_column(String(255), index=True)
    sku: Mapped[str | None] = mapped_column(String(64), index=True)
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    unit_price: Mapped[float] = mapped_column(Float, default=0.0)
    amount: Mapped[float] = mapped_column(Float, default=0.0)

    document: Mapped["Document"] = relationship(back_populates="items")


# ── Sprint 3: Báo cáo cửa hàng theo ngày ─────────────────────────────────────

class StoreReport(Base):
    """Báo cáo tổng kết 1 cửa hàng trong 1 ngày.

    image_hash: sha256 hex (64 chars) của bytes ảnh gốc — dedup cùng group_id + image_hash.
    branch: tên cơ sở (từ caption DM hoặc đọc từ ảnh).
    Tiền lưu ở VND (vision đã ×1000 từ sheet nghìn đồng).
    """

    __tablename__ = "store_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_id: Mapped[int | None] = mapped_column(ForeignKey("scans.id", ondelete="SET NULL"), nullable=True)
    group_id: Mapped[str | None] = mapped_column(String(64), index=True)
    report_date: Mapped[date | None] = mapped_column(Date, index=True)
    branch: Mapped[str | None] = mapped_column(String(128), index=True)
    image_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Tổng hợp tài chính (VND)
    gross_revenue: Mapped[int] = mapped_column(BigInteger, default=0)
    cost: Mapped[int] = mapped_column(BigInteger, default=0)
    net_revenue: Mapped[int] = mapped_column(BigInteger, default=0)
    cash: Mapped[int] = mapped_column(BigInteger, default=0)
    transfer: Mapped[int] = mapped_column(BigInteger, default=0)
    discrepancy: Mapped[int] = mapped_column(BigInteger, default=0)

    created_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=text("CURRENT_TIMESTAMP"))

    channels: Mapped[list["ReportChannel"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )
    products: Mapped[list["ReportProduct"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )
    inventory: Mapped[list["ReportInventory"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Dedup: cùng group_id + image_hash chỉ lưu 1 lần.
        # NULL hash không bị ràng buộc (SQLite/Postgres đều coi NULL distinct trong UNIQUE).
        UniqueConstraint("group_id", "image_hash", name="uq_store_reports_group_hash"),
    )


class ReportChannel(Base):
    """Doanh thu + số lượng theo kênh bán trong 1 báo cáo ngày."""

    __tablename__ = "report_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("store_reports.id"), index=True)
    channel: Mapped[str] = mapped_column(String(32))          # cua_hang|grab|now_shopee|xanh|be
    revenue: Mapped[int] = mapped_column(BigInteger, default=0)
    banh_qty: Mapped[float] = mapped_column(Float, default=0.0)
    nuoc_qty: Mapped[float] = mapped_column(Float, default=0.0)

    report: Mapped["StoreReport"] = relationship(back_populates="channels")


class ReportProduct(Base):
    """Số lượng bán từng sản phẩm theo kênh trong 1 báo cáo ngày."""

    __tablename__ = "report_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("store_reports.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    category: Mapped[str | None] = mapped_column(String(32))   # banh|topping|nuoc
    qty_grab: Mapped[float] = mapped_column(Float, default=0.0)
    qty_now_shopee: Mapped[float] = mapped_column(Float, default=0.0)
    qty_xanh: Mapped[float] = mapped_column(Float, default=0.0)
    qty_be: Mapped[float] = mapped_column(Float, default=0.0)
    qty_cua_hang: Mapped[float] = mapped_column(Float, default=0.0)
    qty_total: Mapped[float] = mapped_column(Float, default=0.0)

    report: Mapped["StoreReport"] = relationship(back_populates="products")


class ReportInventory(Base):
    """Tồn kho đầu/cuối ngày cho từng nguyên liệu/sản phẩm."""

    __tablename__ = "report_inventory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("store_reports.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    open_qty: Mapped[float] = mapped_column(Float, default=0.0)
    import_qty: Mapped[float] = mapped_column(Float, default=0.0)
    discard_qty: Mapped[float] = mapped_column(Float, default=0.0)
    close_qty: Mapped[float] = mapped_column(Float, default=0.0)

    report: Mapped["StoreReport"] = relationship(back_populates="inventory")

"""Smoke test offline: DB + lưu + tổng hợp + format (KHÔNG cần API key/Zalo).

Dùng:  python -m scripts.smoke
"""
from __future__ import annotations

from datetime import date

from bot import formatting as fmt
from db import repository as repo
from db.database import init_db
from nlq.periods import resolve

today = date.today().isoformat()


def main() -> None:
    init_db()

    # Giả lập 2 hoá đơn + 1 đơn hàng trong cùng 1 "nhóm"
    gid = "smoke-group"
    doc = repo.save_extraction(
        group_id=gid, sender_id="u1", sender_name="Linh", image_url=None,
        data={
            "doc_type": "sale", "confidence": 0.9, "doc_date": today,
            "party_name": "Anh Tuấn", "total_amount": 450000, "currency": "VND",
            "items": [
                {"product_name": "Áo thun", "sku": "AT01", "quantity": 2, "unit_price": 150000, "amount": 300000},
                {"product_name": "Nón", "sku": "N01", "quantity": 1, "unit_price": 150000, "amount": 150000},
            ],
        },
    )
    # Xác nhận dùng được doc sau khi session đóng (không DetachedInstanceError)
    print(fmt.saved_block("sale", doc))
    print()
    repo.save_extraction(
        group_id=gid, sender_id="u2", sender_name="Hà", image_url=None,
        data={
            "doc_type": "sale", "confidence": 0.8, "doc_date": today,
            "party_name": "Chị Mai", "total_amount": 300000, "currency": "VND",
            "items": [
                {"product_name": "Áo thun", "sku": "AT01", "quantity": 2, "unit_price": 150000, "amount": 300000},
            ],
        },
    )
    repo.save_extraction(
        group_id=gid, sender_id="u1", sender_name="Linh", image_url=None,
        data={
            "doc_type": "order", "confidence": 0.85, "doc_date": today,
            "party_name": "Anh Nam", "total_amount": 200000, "currency": "VND",
            "status": "cho_giao", "tracking_code": "VN123456",
            "items": [{"product_name": "Nón", "sku": "N01", "quantity": 1, "unit_price": 200000, "amount": 200000}],
        },
    )

    (start, end), label = resolve("today")
    print(fmt.revenue_block(label, repo.revenue_summary(gid, start, end)))
    print()
    print(fmt.sellers_block(label, repo.revenue_by_seller(gid, start, end)))
    print()
    print(fmt.top_products_block(label, repo.top_products(gid, start, end, 5)))
    print()
    print(fmt.orders_block(label, repo.orders_by_status(gid, start, end)))

    assert repo.revenue_summary(gid, start, end)["total"] == 750000, "tổng doanh thu sai"
    assert repo.revenue_summary(gid, start, end)["count"] == 2, "số hoá đơn sai"
    print("\n✅ Smoke test PASSED")


if __name__ == "__main__":
    main()

# Zalo Bot Scanner

Bot Zalo quét ảnh hoá đơn/đơn hàng bằng **Vision AI**, tự bóc dữ liệu ra JSON, lưu vào
database và trả lời **thống kê** khi được tag `@bot` hoặc gọi lệnh trong nhóm Zalo.

- **Zalo Bot API chính thức** (`python-zalo-bot`, long polling)
- **Trích xuất**: vision model qua **Beeknoee** (OpenAI-compatible) — mặc định `gemini-2.5-flash-lite` (~8–15đ/ảnh)
- **Lưu trữ**: SQLAlchemy — mặc định SQLite, đổi 1 dòng sang Postgres/Supabase
- **Loại chứng từ**: hoá đơn bán hàng (`sale`) + đơn hàng/vận đơn (`order`)

## Kiến trúc

```
Nhóm Zalo ──ảnh(+@bot/caption)──▶ on_photo ─▶ Vision AI ─▶ JSON ─▶ lưu (scans/documents/line_items)
User tag @bot / lệnh ──────────▶ on_text/command ─▶ NL router ─▶ truy vấn tổng hợp ─▶ trả lời
```

| Thư mục | Vai trò |
|---|---|
| `config/` | Đọc `.env`, cấu hình tập trung |
| `vision/` | Client Beeknoee + prompt bóc JSON từ ảnh |
| `db/` | Model (Scan / Document / LineItem), session, truy vấn tổng hợp |
| `nlq/` | Hiểu câu hỏi tiếng Việt → chọn thống kê (không sinh SQL tự do) |
| `bot/` | Handler Zalo, định dạng câu trả lời, lớp tương thích SDK (`zalo_compat.py`) |

## Cài đặt

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # rồi điền các key bên dưới
```

`.env` cần điền:
- `ZALO_BOT_TOKEN` — tạo bot tại https://bot.zaloplatforms.com, thêm bot vào nhóm
- `BEEKNOEE_API_KEY` — lấy tại https://platform.beeknoee.com
- (tuỳ chọn) `VISION_MODEL`, `NLQ_MODEL`, `DATABASE_URL`, `SCAN_MODE`

## Chạy

```bash
python main.py
```

## Kiểm thử

```bash
# 1) Logic DB + tổng hợp + format (KHÔNG cần key)
python -m scripts.smoke

# 2) Bóc dữ liệu 1 ảnh thật (cần BEEKNOEE_API_KEY)
python -m scripts.test_extract path/to/hoadon.jpg
```

## Cách dùng trong nhóm

- **Gửi ảnh** kèm `@bot` hoặc caption có chữ *lưu/quét/scan/chốt* → bot đọc & lưu, phản hồi
  `✅ Đã lưu hoá đơn #12 …`. (Đổi `SCAN_MODE=auto` để quét mọi ảnh.)
- **Lệnh**:
  - `/thongke [hôm nay|tuần này|tháng này]` — tổng quan
  - `/doanhthu [khoảng]` — doanh thu
  - `/top [n]` — sản phẩm bán chạy
  - `/donhang [khoảng]` — đơn theo trạng thái
  - `/xoa <id>` — xoá bản ghi sai
- **Hỏi tự do**: `@bot doanh thu tháng này bao nhiêu?`

## Ghi chú kỹ thuật

- Beeknoee **không có endpoint OCR riêng** — OCR chạy qua vision model ở `/v1/chat/completions`
  (vừa đọc chữ vừa bóc thẳng ra JSON, tốt hơn OCR thô).
- Mọi thao tác chạm SDK Zalo gom trong `bot/zalo_compat.py`. Nếu Zalo đổi cách trả ảnh
  (hiện dùng `message.photo_url`), chỉ cần sửa file này.
- Chuyển sang Postgres/Supabase: đổi `DATABASE_URL=postgresql+psycopg://...` (production nên thêm Alembic migration thay cho `create_all`).

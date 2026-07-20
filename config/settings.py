"""Cấu hình tập trung, đọc từ biến môi trường / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


@dataclass(frozen=True)
class Settings:
    zalo_bot_token: str = _get("ZALO_BOT_TOKEN")

    beeknoee_api_key: str = _get("BEEKNOEE_API_KEY")
    beeknoee_base_url: str = _get("BEEKNOEE_BASE_URL", "https://platform-api.beeknoee.com/v1")
    vision_model: str = _get("VISION_MODEL", "gemini-2.5-flash-lite")
    vision_fallback_model: str = _get("VISION_FALLBACK_MODEL", "gemini-2.5-flash")
    nlq_model: str = _get("NLQ_MODEL", "gemini-2.5-flash-lite")

    database_url: str = _get("DATABASE_URL", "sqlite:///data/scanner.db")

    scan_mode: str = _get("SCAN_MODE", "mention")  # "mention" | "auto"
    confirm_threshold: float = float(_get("CONFIRM_THRESHOLD", "0.6") or 0.6)

    # Gộp nhiều ảnh cùng 1 lượt gửi (báo cáo cửa hàng bị chụp tách nhiều ảnh)
    # thành 1 báo cáo: ảnh gửi liên tiếp bởi CÙNG người trong nhóm, trong
    # khoảng này (giây) sẽ ghép chung. 0 = tắt (mỗi ảnh 1 báo cáo).
    report_merge_window_sec: int = int(_get("REPORT_MERGE_WINDOW_SEC", "180") or 180)

    def require(self) -> None:
        """Kiểm tra các key bắt buộc trước khi khởi động."""
        missing = [
            name
            for name, val in (
                ("ZALO_BOT_TOKEN", self.zalo_bot_token),
                ("BEEKNOEE_API_KEY", self.beeknoee_api_key),
            )
            if not val
        ]
        if missing:
            raise SystemExit(
                "Thiếu biến môi trường: "
                + ", ".join(missing)
                + "\n→ Copy .env.example thành .env rồi điền giá trị."
            )


settings = Settings()

"""Test bóc dữ liệu từ 1 ảnh (cần BEEKNOEE_API_KEY).

Dùng:  python -m scripts.test_extract path/to/bill.jpg
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from vision.extractor import extract_document


async def _run(path: str) -> None:
    img = Path(path).read_bytes()
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    data = await extract_document(img, mime=mime)
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Dùng: python -m scripts.test_extract <đường-dẫn-ảnh>")
    asyncio.run(_run(sys.argv[1]))

"""Client gọi Beeknoee AI (OpenAI-compatible: /v1/chat/completions)."""
from __future__ import annotations

import base64
import json
from typing import Any

import httpx

from config.settings import settings


class BeeknoeeError(RuntimeError):
    pass


async def _chat(messages: list[dict], model: str, *, json_mode: bool = True) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {settings.beeknoee_api_key}",
        "Content-Type": "application/json",
    }
    url = f"{settings.beeknoee_base_url.rstrip('/')}/chat/completions"

    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            raise BeeknoeeError(f"HTTP {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:  # pragma: no cover
        raise BeeknoeeError(f"Phản hồi không hợp lệ: {json.dumps(data)[:500]}") from e


async def vision_json(
    image_bytes: bytes,
    system_prompt: str,
    user_prompt: str,
    *,
    mime: str = "image/jpeg",
    model: str | None = None,
) -> dict:
    """Gửi ảnh + prompt tới vision model, ép trả về JSON object.

    Args:
        image_bytes: Nội dung nhị phân của ảnh hoặc PDF.
        system_prompt: System prompt hướng dẫn model.
        user_prompt:   Nội dung yêu cầu của người dùng.
        mime:          MIME type (image/jpeg, image/png, application/pdf, …).
        model:         Tên model override; None → dùng settings.vision_model.
    """
    _model = model if model is not None else settings.vision_model
    b64 = base64.b64encode(image_bytes).decode()
    data_uri = f"data:{mime};base64,{b64}"
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ],
        },
    ]
    return _loads(await _chat(messages, _model))


async def text_json(system_prompt: str, user_prompt: str) -> dict:
    """Chat text-only, trả về JSON (dùng cho định tuyến câu hỏi thống kê)."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    return _loads(await _chat(messages, settings.nlq_model))


def _loads(content: str) -> dict:
    content = content.strip()
    # Gỡ rào ```json ... ``` nếu model bọc thêm
    if content.startswith("```"):
        content = content.split("```", 2)[1]
        content = content[4:] if content.lower().startswith("json") else content
        content = content.strip("` \n")
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise BeeknoeeError(f"Model không trả JSON hợp lệ: {content[:300]}") from e

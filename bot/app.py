"""Dựng ứng dụng Zalo Bot và đăng ký handler."""
from __future__ import annotations

import logging

from zalo_bot.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from bot import handlers as h
from config.settings import settings

log = logging.getLogger("bot")


def build_application():
    app = ApplicationBuilder().token(settings.zalo_bot_token).build()

    app.add_handler(CommandHandler("start", h.cmd_start))
    app.add_handler(CommandHandler("help", h.cmd_help))
    app.add_handler(CommandHandler("thongke", h.cmd_thongke))
    app.add_handler(CommandHandler("doanhthu", h.cmd_doanhthu))
    app.add_handler(CommandHandler("top", h.cmd_top))
    app.add_handler(CommandHandler("donhang", h.cmd_donhang))
    app.add_handler(CommandHandler("xoa", h.cmd_xoa))
    app.add_handler(CommandHandler("baocao", h.cmd_baocao))
    app.add_handler(CommandHandler("khach", h.cmd_khach))
    app.add_handler(CommandHandler("kenh", h.cmd_kenh))
    app.add_handler(CommandHandler("tonkho", h.cmd_tonkho))
    app.add_handler(CommandHandler("coso", h.cmd_coso))

    # Ảnh → quét; text (không phải lệnh) → hỏi tự do
    app.add_handler(MessageHandler(filters.PHOTO, h.on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, h.on_text))

    return app

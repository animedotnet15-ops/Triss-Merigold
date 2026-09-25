"""
triss.handlers.settings
========================
Owner-only entrypoint for the fully button-based settings panel. All
actual submenu logic lives in `triss.handlers.callbacks`, which handles
every `settings:*`, `welcome:*`, `forcesub:*`, `autodelete:*`,
`maintenance:*`, `backup:*`, and `storechannel:*` callback query.
"""

from __future__ import annotations

from pyrogram import filters
from pyrogram.types import Message

from triss.bot import app
from triss.database import models as db
from triss.utils.auth import deny_if_not_owner
from triss.utils.keyboards import settings_main_menu

SETTINGS_HEADER = (
    "⚙️ ᴛʀɪss sᴇᴛᴛɪɴɢs\n\n"
    "Choose a category below. Everything here is button-driven — no typed commands needed."
)


@app.on_message(filters.command("settings") & filters.private)
async def settings_command(client, message: Message) -> None:
    if await deny_if_not_owner(message):
        return
    settings = await db.get_settings()
    total_users = await db.count_users()
    header = f"{SETTINGS_HEADER}\n\n👥 Total bot users: {total_users:,}"
    await message.reply_text(header, reply_markup=settings_main_menu(settings))
    

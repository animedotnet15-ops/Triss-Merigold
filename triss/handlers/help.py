"""
triss.handlers.help
=====================
/help — ONLY the commands an actual end user runs (/start, /buy_premium,
/rt_save, /help itself). Owner/admin commands all live in /settings
instead (that's their own control surface) - /help stays a single,
simple view for everyone, same text for owner and regular users alike.
Small-caps + bold, matching the rest of the bot's buttons/menus.
"""

from __future__ import annotations

from pyrogram import filters
from pyrogram.types import Message

from triss.bot import app

_EVERYONE_SECTION = """
<b>ᴡᴇʟᴄᴏᴍᴇ! ʜᴇʀᴇ's ᴡʜᴀᴛ ʏᴏᴜ ᴄᴀɴ ᴅᴏ:</b>

<b>/start</b> — ᴏᴘᴇɴs ᴛʜᴇ ʙᴏᴛ / ʀᴇᴅᴇᴇᴍs ᴀ ʟɪɴᴋ sᴏᴍᴇᴏɴᴇ sʜᴀʀᴇᴅ ᴡɪᴛʜ ʏᴏᴜ.

<b>/buy_premium</b> — 💎 sᴋɪᴘ ᴛʜᴇ sʜᴏʀᴛᴇɴᴇʀ (ᴀɴᴅ ғᴏʀᴄᴇ sᴜʙ, ᴏɴ ʜɪɢʜᴇʀ ᴘʟᴀɴs) ᴡɪᴛʜ ᴀ ᴘᴀɪᴅ ᴘʟᴀɴ.

<b>/rt_save</b> — 🔓 ᴏɴᴄᴇ ᴇɴᴀʙʟᴇᴅ ʙʏ ᴛʜᴇ ᴏᴡɴᴇʀ, ᴠᴇʀɪғʏ ᴏɴᴄᴇ ᴛʜᴇɴ sᴇɴᴅ ᴀ ᴛ.ᴍᴇ ʟɪɴᴋ ғʀᴏᴍ ᴀ ʀᴇsᴛʀɪᴄᴛ-sᴀᴠɪɴɢ ᴄʜᴀᴛ ᴛᴏ ɢᴇᴛ ɪᴛ ʙᴀᴄᴋ ᴜɴᴘʀᴏᴛᴇᴄᴛᴇᴅ.

<b>/linkdl</b> — 📥 ᴏɴᴄᴇ ᴇɴᴀʙʟᴇᴅ, sᴇɴᴅ ᴀɴʏ ғɪʟᴇ ᴅɪʀᴇᴄᴛʟʏ ᴀɴᴅ ɢᴇᴛ ᴀ ʀᴇᴀʟ ʙʀᴏᴡsᴇʀ ᴅᴏᴡɴʟᴏᴀᴅ ʟɪɴᴋ ʙᴀᴄᴋ ʀɪɢʜᴛ ᴀᴡᴀʏ — ɴᴏ ɢᴇɴʟɪɴᴋ ɴᴇᴇᴅᴇᴅ.

<b>/help</b> — ᴛʜɪs ᴍᴇɴᴜ.
"""


@app.on_message(filters.command("help") & filters.private)
async def help_cmd(client, message: Message) -> None:
    await message.reply_text(_EVERYONE_SECTION.strip())

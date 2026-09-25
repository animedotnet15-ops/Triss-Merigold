"""
triss.handlers.help
=====================
/help — one usage line + short explanation per command, filtered by
who's actually allowed to run each one, so a regular user only sees
/help itself (they have nothing else to run), and owner/admin see the
full set. Doesn't duplicate the detailed usage already shown when a
command is run with no arguments (e.g. /mute with no target still shows
its own ⚠️ Usage line) — this is the single place to see everything at
once instead of trial-and-erroring each command individually.
"""

from __future__ import annotations

from pyrogram import filters
from pyrogram.types import Message

from triss.bot import app
from triss.utils.auth import is_owner, is_super_owner

_OWNER_ADMIN_SECTION = """
🔗 <b>Links</b>
<code>/genlink</code> — reply with the file/text/link you want, and the bot stores it and gives back one Telegram link that delivers it to anyone who opens it.
<code>/batch</code> — send the FIRST message of a range, then the LAST message, then <code>/done</code>: one link delivers everything in between in order. <code>/cancelbatch</code> cancels a batch in progress.
<code>/linkdl</code> — toggles Direct Download links on/off. While on, send any file directly (no /genlink needed) and get back a real browser download link instead of a Telegram deep link. <code>/setlinkcaption your text {link}</code> customizes the reply (owner only).

👥 <b>Moderation</b> (owner or admin)
<code>/mute user_id [reason]</code> / <code>/unmute user_id</code> / <code>/listmute</code> — blocks a user from redeeming links (reply to their forwarded message instead of typing the id if you prefer).
<code>/ban user_id [reason]</code> / <code>/unban user_id</code> / <code>/listban</code> — a full block: a banned user can't use the bot at all, not even /start.
"""

_SUPER_OWNER_SECTION = """
🛡️ <b>Admins</b> (owner only)
<code>/addadmin user_id</code> / <code>/removeadmin user_id</code> / <code>/listadmin</code> — grants/revokes admin access (same as owner except managing other admins). Reply to a forwarded message instead of typing the id if you prefer.
"""

_EVERYONE_SECTION = """
👋 <b>For everyone</b>
<code>/start</code> — opens the bot / redeems a link someone shared with you.
<code>/help</code> — this menu.
"""


@app.on_message(filters.command("help") & filters.private)
async def help_cmd(client, message: Message) -> None:
    user_id = message.from_user.id
    text = "📖 <b>How to use this bot</b>\n" + _EVERYONE_SECTION
    if is_owner(user_id):
        text += _OWNER_ADMIN_SECTION
    if is_super_owner(user_id):
        text += _SUPER_OWNER_SECTION
    if is_owner(user_id):
        text += "\n⚙️ Everything else (welcome message, force sub, shortener, effects, etc.) lives in <code>/settings</code>."
    await message.reply_text(text.strip())

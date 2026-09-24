"""
triss.handlers.linkdl
=======================
Item 6 — /linkdl: while enabled ("🌍 Public Use" toggle), any file the
owner/admin sends directly to the bot (outside of an active /genlink or
/batch session) is automatically copied into LOG_CHANNEL_ID and turned
into a REAL, browser-downloadable HTTP link (triss/web/server.py's
/dl/<token> route) — unlike every other link in this bot, which
delivers via a Telegram deep link instead. Requires PUBLIC_BASE_URL to
be configured (see triss/config.py) since a browser download needs a
real public URL to hit; every other feature in this bot works without it.

NO DATABASE RECORD IS CREATED for a linkdl link (explicit spec: "database
la add aga vendam"). The file's only home is LOG_CHANNEL_ID, and the
download token is a stateless, HMAC-signed reference to that message id
(see triss.utils.linkdl_token) — there is nothing to look up, so nothing
to store. The trade-off (documented in linkdl_token.py) is that an
individual linkdl link can't be revoked/expired later, only turned off
globally via the Public Use toggle.

Only the owner (not even other admins) can change the caption template,
per spec ("caption ower mattum edit pandra mari venum") — toggling
/linkdl itself is available to owner AND admin like other
moderation-adjacent actions.
"""

from __future__ import annotations

import logging

from pyrogram import filters
from pyrogram.errors import RPCError
from pyrogram.types import Message, InlineKeyboardMarkup

from triss.bot import app
from triss.config import config
from triss.database import models as db
from triss.services.cleanup import session_manager
from triss.utils.auth import owner_filter, deny_if_not_owner, deny_if_not_super_owner
from triss.utils.keyboards import url_btn
from triss.utils.linkdl_token import encode_linkdl_token
from triss.utils.effects import resolve_effect_id, call_with_optional_effect

logger = logging.getLogger("triss.handlers.linkdl")

DEFAULT_LINKDL_CAPTION = "🔗 <b>Direct Download Link</b>\n\n{link}"

_MEDIA_FILTER = (
    filters.photo | filters.video | filters.document | filters.audio |
    filters.animation | filters.voice | filters.video_note
)

_EXCLUDED_COMMANDS = [
    "genlink", "batch", "done", "cancelbatch", "broadcast", "settings", "start",
    "linkdl", "setlinkcaption", "addadmin", "removeadmin", "listadmin",
    "mute", "unmute", "listmute",
]


def build_download_url(message_id: int) -> str | None:
    """None if PUBLIC_BASE_URL isn't configured — callers must treat that
    as 'feature unavailable', never fabricate a broken URL."""
    if not config.public_base_url:
        return None
    return f"{config.public_base_url}/dl/{encode_linkdl_token(message_id)}"


async def _no_active_session(_, __, message: Message) -> bool:
    user = message.from_user
    return user is not None and session_manager.get(user.id) is None


no_session_filter = filters.create(_no_active_session)


@app.on_message(filters.command("linkdl") & filters.private)
async def linkdl_toggle_cmd(client, message: Message) -> None:
    if await deny_if_not_owner(message):
        return
    if not config.public_base_url:
        await message.reply_text(
            "⚠️ <code>PUBLIC_BASE_URL</code> isn't configured, so direct download links "
            "have nowhere public to be hosted yet. Set it to this bot's own public HTTPS "
            "URL (Railway/Render give you one automatically) and restart, then try /linkdl again.\n\n"
            "Every other feature in this bot works fine without it — this is the only one that needs it."
        )
        return
    if not config.log_channel_id:
        await message.reply_text(
            "⚠️ <code>LOG_CHANNEL_ID</code> isn't configured — /linkdl stores files there "
            "instead of a database, so it's required for this feature specifically."
        )
        return
    settings = await db.get_settings()
    new_value = not settings.get("linkdl", {}).get("enabled", False)
    await db.update_settings({"linkdl.enabled": new_value})
    if new_value:
        await message.reply_text(
            "✅ Direct download links <b>enabled</b> (🌍 Public Use: ON). Send me any file/"
            "document/video directly (no /genlink needed) and I'll reply with a link that "
            "downloads straight in the browser.\n\n"
            "Owner can customize the reply caption with <code>/setlinkcaption</code>."
        )
    else:
        await message.reply_text(
            "🚫 Direct download links <b>disabled</b> (🌍 Public Use: OFF) — every existing "
            "/dl link stops working immediately too, not just new ones."
        )


@app.on_message(filters.command("setlinkcaption") & filters.private)
async def setlinkcaption_cmd(client, message: Message) -> None:
    if await deny_if_not_super_owner(message):
        return
    if len(message.command) < 2:
        await message.reply_text(
            "⚠️ <b>Usage:</b> <code>/setlinkcaption your text with {link}</code>\n\n"
            f"Example default: <code>{DEFAULT_LINKDL_CAPTION}</code>\n\n"
            "Send <code>/setlinkcaption reset</code> to go back to the default."
        )
        return
    raw = message.text.split(None, 1)[1]
    if raw.strip().lower() == "reset":
        await db.update_settings({"linkdl.caption": None})
        await message.reply_text("✅ Caption reset to default.")
        return
    if "{link}" not in raw:
        await message.reply_text("⚠️ Your caption must include a <code>{link}</code> placeholder.")
        return
    await db.update_settings({"linkdl.caption": message.text.html.split(None, 1)[1]})
    await message.reply_text("✅ Direct-download caption updated.")


@app.on_message(
    filters.private & owner_filter & no_session_filter & _MEDIA_FILTER
    & ~filters.command(_EXCLUDED_COMMANDS)
)
async def linkdl_capture(client, message: Message) -> None:
    """Dormant no-op unless linkdl.enabled is True — the no_session_
    filter above already guarantees this never fires while /genlink,
    /batch, or any settings-capture flow is active for this user, so
    enabling this feature can never steal a message meant for one of
    those."""
    settings = await db.get_settings()
    linkdl = settings.get("linkdl", {})
    if not linkdl.get("enabled"):
        return
    if not config.public_base_url or not config.log_channel_id:
        await message.reply_text(
            "⚠️ Direct downloads are enabled but not fully configured — "
            "ask the owner to run /linkdl again for details."
        )
        return

    # The log channel copy IS the file's only storage for this feature —
    # no Store Channel, no database record (see module docstring).
    try:
        copied = await client.copy_message(config.log_channel_id, message.chat.id, message.id)
    except RPCError:
        logger.exception("linkdl: failed to copy file into LOG_CHANNEL_ID.")
        await message.reply_text("⚠️ Couldn't store that file — check that I'm still in the log channel.")
        return

    download_url = build_download_url(copied.id)
    caption_template = linkdl.get("caption") or DEFAULT_LINKDL_CAPTION
    caption = caption_template.replace("{link}", download_url)

    linkdl_effect = settings.get("linkdl_effect", {})
    effect_id = resolve_effect_id(linkdl_effect.get("effect")) if linkdl_effect.get("enabled") else None

    await call_with_optional_effect(
        message.reply_text,
        text=caption,
        reply_markup=InlineKeyboardMarkup([[url_btn("⬇️ Download", download_url)]]),
        message_effect_id=effect_id,
    )
    

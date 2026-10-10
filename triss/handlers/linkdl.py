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
from triss.utils.linkdl_token import encode_linkdl_token, decode_linkdl_token
from triss.services.logging_service import log_linkdl_files

logger = logging.getLogger("triss.handlers.linkdl")

LINKDL_START_PREFIX = "dl_"
DEFAULT_LINKDL_CAPTION = "🔗 <b>Direct Download Link</b>\n\n{link}"

_MEDIA_FILTER = (
    filters.photo | filters.video | filters.document | filters.audio |
    filters.animation | filters.voice | filters.video_note
)

_EXCLUDED_COMMANDS = [
    "genlink", "batch", "done", "cancelbatch", "broadcast", "settings", "start",
    "linkdl", "setlinkcaption", "buy_premium", "setvip", "listvip", "deletevip", "search", "rt_save", "addadmin", "removeadmin", "listadmin",
    "mute", "unmute", "listmute", "ban", "unban", "listban", "autobatch", "help",
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


_ENABLED_TEXT = (
    "<b>✅ ᴅɪʀᴇᴄᴛ ᴅᴏᴡɴʟᴏᴀᴅ ʟɪɴᴋs ᴇɴᴀʙʟᴇᴅ (ᴘᴜʙʟɪᴄ ᴜsᴇ: ᴏɴ)</b>\n\n"
    "Send me any file/document/video directly (no /genlink needed) and I'll reply "
    "with the direct link right away — no extra steps.\n\n"
    "Owner can customize the reply caption with <code>/setlinkcaption</code>.\n\n"
    "Turn it off with <code>/linkdl off</code>."
)
_DISABLED_TEXT = (
    "<b>🚫 ᴅɪʀᴇᴄᴛ ᴅᴏᴡɴʟᴏᴀᴅ ʟɪɴᴋs ᴅɪsᴀʙʟᴇᴅ (ᴘᴜʙʟɪᴄ ᴜsᴇ: ᴏғғ)</b>\n\n"
    "Every existing /dl link stops working immediately too, not just new ones.\n\n"
    "Turn it on with <code>/linkdl on</code>."
)


@app.on_message(filters.command("linkdl") & filters.private)
async def linkdl_toggle_cmd(client, message: Message) -> None:
    """/linkdl (no argument) ONLY reports current status now — it used to
    flip the toggle on every call, so running it twice in a row to just
    check status silently turned the feature back off. Use /linkdl on
    or /linkdl off to actually change it (same as the Settings button)."""
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
    if not await db.get_active_channel_id("log"):
        await message.reply_text(
            "⚠️ No Log Channel is set (\u2699\ufe0f /settings -> Log Channel) — /linkdl stores files there "
            "instead of a database, so it's required for this feature specifically."
        )
        return

    args = message.text.split(maxsplit=1)
    arg = args[1].strip().lower() if len(args) > 1 else None
    settings = await db.get_settings()
    current = settings.get("linkdl", {}).get("enabled", False)

    if arg not in ("on", "off"):
        await message.reply_text(_ENABLED_TEXT if current else _DISABLED_TEXT)
        return

    new_value = arg == "on"
    if new_value == current:
        await message.reply_text(_ENABLED_TEXT if current else _DISABLED_TEXT)
        return
    await db.update_settings({"linkdl.enabled": new_value})
    await message.reply_text(_ENABLED_TEXT if new_value else _DISABLED_TEXT)


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
        await message.reply_text(
            "<b>🚫 ᴅɪʀᴇᴄᴛ ᴅᴏᴡɴʟᴏᴀᴅ ʟɪɴᴋs ᴅɪsᴀʙʟᴇᴅ (ᴘᴜʙʟɪᴄ ᴜsᴇ: ᴏғғ)</b>\n\n"
            "Turn it on with /linkdl first, then send the file again."
        )
        return
    log_channel_id = await db.get_active_channel_id("log")
    if not config.public_base_url or not log_channel_id:
        await message.reply_text(
            "⚠️ Direct downloads are enabled but not fully configured — "
            "ask the owner to run /linkdl again for details."
        )
        return

    # The log channel copy IS the file's only storage for this feature —
    # no Store Channel, no database record (see module docstring).
    try:
        copied = await client.copy_message(log_channel_id, message.chat.id, message.id)
    except RPCError:
        logger.exception("linkdl: failed to copy file into LOG_CHANNEL_ID.")
        await message.reply_text("⚠️ Couldn't store that file — check that I'm still in the log channel.")
        return

    user = message.from_user
    download_url = build_download_url(copied.id)
    await log_linkdl_files(client, user.id, user.username, user.first_name, delivered_link=download_url)

    # No bot-link hop, no Force Sub gate - the direct browser link comes
    # back immediately, every time, as soon as the file is sent. Public
    # Use ON/OFF (checked above, before this point) is the only gate.
    await send_linkdl_reply(client, message.chat.id, linkdl, download_url, download_url)


async def send_linkdl_reply(client, chat_id: int, linkdl: dict, shown_link: str, button_url: str) -> None:
    """Sends the (owner-customizable) linkdl reply: caption template with
    {link} replaced by `shown_link`, optional photo/video/gif + spoiler,
    and a Download button pointing at `button_url`."""
    caption_template = linkdl.get("caption") or DEFAULT_LINKDL_CAPTION
    caption = caption_template.replace("{link}", shown_link)
    media_type = linkdl.get("media_type", "none")
    spoiler = bool(linkdl.get("spoiler"))
    btn_markup = InlineKeyboardMarkup([[url_btn("⬇️ Download", button_url)]])

    if media_type == "video" and linkdl.get("video_file_id"):
        await client.send_video(
            chat_id=chat_id, video=linkdl["video_file_id"],
            caption=caption, has_spoiler=spoiler, reply_markup=btn_markup,
        )
    elif media_type == "animation" and linkdl.get("animation_file_id"):
        await client.send_animation(
            chat_id=chat_id, animation=linkdl["animation_file_id"],
            caption=caption, has_spoiler=spoiler, reply_markup=btn_markup,
        )
    elif media_type == "photo" and linkdl.get("photo_file_id"):
        await client.send_photo(
            chat_id=chat_id, photo=linkdl["photo_file_id"],
            caption=caption, has_spoiler=spoiler, reply_markup=btn_markup,
        )
    else:
        await client.send_message(chat_id=chat_id, text=caption, reply_markup=btn_markup)


async def deliver_linkdl(client, chat_id: int, token: str) -> None:
    """Public side of a gated linkdl link (/start dl_<token>) - called
    only AFTER Force Sub has been satisfied. Reveals the real browser
    download URL."""
    message_id = decode_linkdl_token(token)
    settings = await db.get_settings()
    linkdl = settings.get("linkdl", {})
    if message_id is None or not linkdl.get("enabled") or not config.public_base_url:
        await client.send_message(chat_id, "⚠️ This download link is invalid or currently disabled.")
        return
    download_url = build_download_url(message_id)
    await send_linkdl_reply(client, chat_id, linkdl, download_url, download_url)

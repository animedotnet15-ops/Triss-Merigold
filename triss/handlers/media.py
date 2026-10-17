"""
triss.handlers.media
======================
📱 /media — downloads a post from YouTube, Instagram, TikTok, Twitter(X),
or Facebook (via yt-dlp - no third-party API key needed) and sends it
back to whoever asked.

Hard limit: Telegram's cloud Bot API refuses any file a bot tries to
upload over 50MB - this is a Telegram-side limit, not something this
bot's code can lift (only a self-hosted Local Bot API Server raises it
to 2000MB, which is a separate server/infrastructure, not a code
change - see the /media settings help text). A video that comes out
larger than that gets deleted locally and the user is told it's too
big, rather than the bot trying and failing to upload it.

Gating, in order: Public Use (settings.media.enabled) -> Force Sub
(master force_sub_enabled AND fs_manage.media - same pattern as
triss.handlers.start's rt_save gate).
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile

from pyrogram import filters
from pyrogram.types import Message

from triss.bot import app
from triss.database import models as db
from triss.services import forcesub
from triss.services.cleanup import session_manager, session_is
from triss.utils.formatting import FORCE_SUB_TEXT
from triss.utils.keyboards import cancel_only, force_sub_user_keyboard

logger = logging.getLogger("triss.handlers.media")

MAX_BYTES = 50 * 1024 * 1024  # Telegram Bot API's hard upload limit

_COMMANDS = [
    "genlink", "batch", "done", "cancelbatch", "autobatch", "broadcast", "settings", "start",
    "addadmin", "removeadmin", "listadmin", "mute", "unmute", "listmute",
    "ban", "unban", "listban", "linkdl", "setlinkcaption", "buy_premium",
    "setvip", "listvip", "deletevip", "search", "rt_save", "media", "delfilter", "profile", "redeem", "givepoints", "removepoints", "addquote", "help",
]


async def _force_sub_blocks(client, message: Message, user_id: int, settings: dict) -> bool:
    """True (and already sent the join prompt) if Force Sub should stop
    this request. Premium Silver/Gold still skip Force Sub entirely -
    reuses the same check as every other gated feature."""
    if not settings.get("force_sub_enabled", True):
        return False
    if not settings.get("fs_manage", {}).get("media", True):
        return False
    from triss.handlers.start import _premium_skips_force_sub
    if await _premium_skips_force_sub(user_id):
        return False
    unsatisfied = await forcesub.get_unsatisfied_requirements(client, user_id)
    if not unsatisfied:
        return False
    entries = await forcesub.get_display_entries()
    from triss.handlers.start import _send_customizable_popup
    await _send_customizable_popup(
        client, message, settings.get("force_sub_message", {}), FORCE_SUB_TEXT,
        reply_markup=force_sub_user_keyboard(entries), user_id=user_id,
    )
    return True


@app.on_message(filters.command("media") & filters.private)
async def media_cmd(client, message: Message) -> None:
    user_id = message.from_user.id
    settings = await db.get_settings()
    if not settings.get("media", {}).get("enabled"):
        await message.reply_text("⚠️ This feature is currently disabled.")
        return
    if await _force_sub_blocks(client, message, user_id, settings):
        return

    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        await _handle_media_link(client, message, args[1].strip())
        return

    session_manager.set(user_id, "media_awaiting_link")
    await message.reply_text(
        "📱 Send the link now — YouTube, Instagram, TikTok, Twitter(X), or Facebook.",
        reply_markup=cancel_only("generic:cancel"),
    )


@app.on_message(filters.private & session_is("media_awaiting_link")
                 & filters.text & ~filters.command(_COMMANDS))
async def capture_media_link(client, message: Message) -> None:
    session_manager.clear(message.from_user.id)
    await _handle_media_link(client, message, (message.text or "").strip())


def _blocking_download(url: str, out_dir: str) -> str:
    """Runs in a thread executor - yt_dlp is fully synchronous. Picks a
    single progressive (pre-merged audio+video) format so no ffmpeg
    post-processing step is required on the host."""
    import yt_dlp

    out_template = os.path.join(out_dir, "%(id)s.%(ext)s")
    options = {
        "format": "best[ext=mp4]/best",
        "outtmpl": out_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": MAX_BYTES,
        "retries": 2,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)


async def _handle_media_link(client, message: Message, url: str) -> None:
    if not url.startswith(("http://", "https://")):
        await message.reply_text("⚠️ That doesn't look like a link. Send a post/video URL.")
        return

    status = await message.reply_text("⏳ Downloading...")
    with tempfile.TemporaryDirectory(prefix="triss_media_") as tmp_dir:
        try:
            loop = asyncio.get_event_loop()
            file_path = await loop.run_in_executor(None, _blocking_download, url, tmp_dir)
        except Exception as e:
            logger.warning("media: download failed for %s.", url, exc_info=True)
            err = str(e)
            if "max_filesize" in err or "File is larger than max-filesize" in err:
                await status.edit_text("⚠️ That video is over 50MB — Telegram won't let the bot send it.")
            else:
                await status.edit_text(
                    "⚠️ Couldn't download that - the link may be private, region-locked, "
                    "or from a platform that changed how it serves content."
                )
            return

        if not os.path.exists(file_path):
            await status.edit_text("⚠️ Download finished but the file is missing. Try again.")
            return
        size = os.path.getsize(file_path)
        if size > MAX_BYTES:
            await status.edit_text("⚠️ That video is over 50MB — Telegram won't let the bot send it.")
            return

        try:
            await status.edit_text("⬆️ Uploading...")
            if file_path.lower().endswith((".mp4", ".mkv", ".webm", ".mov")):
                await client.send_video(message.chat.id, file_path)
            else:
                await client.send_document(message.chat.id, file_path)
            await status.delete()
        except Exception:
            logger.exception("media: upload failed for %s.", url)
            await status.edit_text("⚠️ Downloaded fine but couldn't upload it to Telegram. Try again.")

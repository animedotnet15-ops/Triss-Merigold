"""
triss.handlers.autobatch
=========================
Owner/admin-only. /autobatch turns a RANGE of posts in a "base channel"
into one batch link, without forwarding every file by hand:

  1. /autobatch  -> bot asks for the FIRST file of the range
  2. owner forwards that file from the base channel (or sends its post link)
  3. bot asks for the LAST file of the range
  4. owner forwards the last file (or sends its post link)
  5. bot copies EVERY message from the first through the last (both
     included) into the active Store Channel, in order, and replies with
     an ordinary batch link (same as /batch's /done).

At most MAX_MESSAGES message ids may be in the range. The bot must be an
admin (or at least able to read) in the base channel. Empty/deleted ids
and service messages inside the range are skipped.
"""

from __future__ import annotations

import asyncio
import logging
import re

from pyrogram import filters
from pyrogram.types import Message, LinkPreviewOptions

from triss.bot import app
from triss.database import models as db
from triss.services.cleanup import session_manager, session_is
from triss.services.storage import store_message, message_ref, StorageError
from triss.utils.auth import owner_filter, deny_if_not_owner
from triss.utils.keyboards import cancel_only, generated_link_keyboard
from triss.utils.tokens import generate_token, build_deep_link

logger = logging.getLogger("triss.handlers.autobatch")

MAX_MESSAGES = 200

_COMMANDS = [
    "genlink", "batch", "done", "cancelbatch", "autobatch", "broadcast", "settings", "start",
    "addadmin", "removeadmin", "listadmin", "mute", "unmute", "listmute",
    "ban", "unban", "listban", "linkdl", "setlinkcaption", "buy_premium", "help",
]

_PRIVATE_LINK_RE = re.compile(r"t\.me/c/(\d+)/(?:\d+/)?(\d+)")
_PUBLIC_LINK_RE = re.compile(r"t\.me/([A-Za-z][A-Za-z0-9_]{3,})/(\d+)")


def _reference_from_message(message: Message):
    """(chat_ref, message_id) of the post the owner pointed at - either a
    forwarded channel post or a t.me post link - or None."""
    try:
        origin = getattr(message, "forward_origin", None)
        chat = mid = None
        if origin is not None:
            c = getattr(origin, "chat", None)
            chat = getattr(c, "sender_chat", None) or c
            mid = getattr(origin, "message_id", None)
        if chat is None:
            chat = getattr(message, "forward_from_chat", None)
            mid = mid or getattr(message, "forward_from_message_id", None)
        if chat is not None and mid:
            return chat.id, int(mid)
    except Exception:
        logger.debug("Could not read forward origin.", exc_info=True)
    text = (message.text or message.caption or "").strip()
    m = _PRIVATE_LINK_RE.search(text)
    if m:
        return int("-100" + m.group(1)), int(m.group(2))
    m = _PUBLIC_LINK_RE.search(text)
    if m:
        return m.group(1), int(m.group(2))
    return None


async def _resolve(client, message: Message):
    """Returns the actual base-channel Message the owner pointed at, or
    None after telling the owner what went wrong."""
    ref = _reference_from_message(message)
    if ref is None:
        await message.reply_text(
            "⚠️ Forward the file from the base channel (forward tag must be visible), "
            "or send that post's link."
        )
        return None
    try:
        base = await client.get_messages(ref[0], ref[1])
    except Exception:
        base = None
    if base is None or base.empty:
        await message.reply_text(
            "⚠️ I couldn't read that post. Make sure I'm an admin in the base channel, then try again."
        )
        return None
    return base


@app.on_message(filters.command("autobatch") & filters.private)
async def autobatch_command(client, message: Message) -> None:
    if await deny_if_not_owner(message):
        return
    if not await db.get_active_channel_id("storage"):
        await message.reply_text("⚠️ No Store Channel is set. Add one in /settings -> Store Channel first.")
        return
    session_manager.set(message.from_user.id, "autobatch_first", {})
    await message.reply_text(
        "📦 <b>Auto Batch</b>\n\n1️⃣ Send the <b>FIRST</b> file: forward it from the base channel "
        "(or send its post link).",
        reply_markup=cancel_only("generic:cancel"),
    )


@app.on_message(filters.private & owner_filter & session_is("autobatch_first") & ~filters.command(_COMMANDS))
async def autobatch_first(client, message: Message) -> None:
    base = await _resolve(client, message)
    if base is None:
        return
    session_manager.set(message.from_user.id, "autobatch_last",
                        {"chat_id": base.chat.id, "first_id": base.id})
    await message.reply_text(
        f"✅ First file saved (id {base.id}).\n\n2️⃣ Now send the <b>LAST</b> file the same way.",
        reply_markup=cancel_only("generic:cancel"),
    )


@app.on_message(filters.private & owner_filter & session_is("autobatch_last") & ~filters.command(_COMMANDS))
async def autobatch_last(client, message: Message) -> None:
    user_id = message.from_user.id
    session = session_manager.get(user_id)
    if session is None:
        return
    base = await _resolve(client, message)
    if base is None:
        return
    chat_id, first_id = session.data["chat_id"], session.data["first_id"]
    if base.chat.id != chat_id:
        await message.reply_text("⚠️ The last file must come from the same base channel as the first. Send it again.")
        return
    last_id = base.id
    if last_id < first_id:
        await message.reply_text("⚠️ That file is OLDER than the first one. Send the newest file of the range.")
        return
    span = last_id - first_id + 1
    if span > MAX_MESSAGES:
        await message.reply_text(
            f"⚠️ That range covers {span} posts - Auto Batch allows at most {MAX_MESSAGES}. "
            "Send an earlier last file, or run /autobatch again."
        )
        return

    session_manager.set(user_id, "autobatch_running", {})
    try:
        await _run(client, message, chat_id, first_id, last_id)
    finally:
        session_manager.clear(user_id)


async def _run(client, message: Message, chat_id: int, first_id: int, last_id: int) -> None:
    user_id = message.from_user.id
    status = await message.reply_text("⏳ Reading the range...")
    try:
        posts = await client.get_messages(chat_id, list(range(first_id, last_id + 1)))
    except Exception as e:
        logger.exception("autobatch: could not read range %s-%s in %s.", first_id, last_id, chat_id)
        await status.edit_text(f"⚠️ Could not read the range: {e}")
        return
    posts = [p for p in posts if p is not None and not p.empty and not p.service]
    if not posts:
        await status.edit_text("⚠️ No files found in that range.")
        return

    refs: list[dict] = []
    total = len(posts)
    for n, post in enumerate(posts, start=1):
        try:
            stored = await store_message(client, post)
        except StorageError as e:
            await _rollback(client, refs)
            await status.edit_text(f"⚠️ Stopped at file {n}/{total}: {e}\n\nNothing was saved.")
            return
        refs.append(message_ref(stored, len(refs)))
        session_manager.touch(user_id)
        if n % 10 == 0:
            try:
                await status.edit_text(f"⏳ Saving files... {n}/{total}")
            except Exception:
                pass
        await asyncio.sleep(0.4)

    token = generate_token()
    await db.create_link(token, "batch", refs)
    username = getattr(client, "username", None) or (await client.get_me()).username
    link = build_deep_link(username, token)
    try:
        await status.delete()
    except Exception:
        pass
    await message.reply_text(
        f"✅ Auto Batch link generated ({len(refs)} item(s)):\n\n<code>{link}</code>",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
        reply_markup=generated_link_keyboard(link),
    )


async def _rollback(client, refs: list[dict]) -> None:
    """Deletes already-copied Store Channel messages so a failed run never
    leaves orphaned storage behind."""
    by_chat: dict[int, list[int]] = {}
    for ref in refs:
        by_chat.setdefault(ref["chat_id"], []).append(ref["message_id"])
    for chat, ids in by_chat.items():
        try:
            await client.delete_messages(chat, ids)
        except Exception:
            logger.warning("autobatch: rollback delete failed in chat %s.", chat, exc_info=True)

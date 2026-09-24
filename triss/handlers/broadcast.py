"""
triss.handlers.broadcast
=========================
Owner-only. /broadcast then a single owner message (any supported type)
is copied to every stored user. Runs as a background task so it never
blocks the bot event loop; handles FloodWait per-recipient, tracks and
reports failures, and removes users who have permanently blocked the
bot so future broadcasts don't keep retrying them forever.
"""

from __future__ import annotations

import asyncio
import logging

from pyrogram import filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated, PeerIdInvalid, RPCError

from triss.bot import app
from triss.config import config
from triss.database import models as db
from triss.services.cleanup import session_manager, session_is
from triss.utils.auth import owner_filter, deny_if_not_owner
from triss.utils.keyboards import cancel_only

logger = logging.getLogger("triss.handlers.broadcast")

_ADMIN_COMMANDS = ["genlink", "batch", "done", "cancelbatch", "broadcast", "settings", "start", "addadmin", "removeadmin", "listadmin", "mute", "unmute", "listmute", "linkdl", "setlinkcaption"]


@app.on_message(filters.command("broadcast") & filters.private)
async def broadcast_command(client, message: Message) -> None:
    if await deny_if_not_owner(message):
        return
    session_manager.set(message.from_user.id, "broadcast_waiting")
    await message.reply_text(
        "📣 Send the message/content you want to broadcast to all users.",
        reply_markup=cancel_only("broadcast:cancel"),
    )


async def _run_broadcast(client, source: Message, owner_chat_id: int) -> None:
    user_ids = await db.get_all_user_ids()
    total = len(user_ids)
    sent = 0
    failed = 0
    blocked_removed = 0

    progress = await client.send_message(owner_chat_id, f"📤 Broadcasting to {total} user(s)...")

    for user_id in user_ids:
        try:
            await source.copy(chat_id=user_id)
            sent += 1
        except FloodWait as e:
            await asyncio.sleep(e.value)
            try:
                await source.copy(chat_id=user_id)
                sent += 1
            except RPCError:
                failed += 1
        except (UserIsBlocked, InputUserDeactivated, PeerIdInvalid):
            await db.delete_user(user_id)
            blocked_removed += 1
        except RPCError:
            logger.warning("Broadcast delivery failed for user %s.", user_id, exc_info=True)
            failed += 1

    try:
        await progress.edit_text(
            f"✅ Broadcast complete.\n\n"
            f"Delivered: {sent}\n"
            f"Failed: {failed}\n"
            f"Removed (blocked/deactivated): {blocked_removed}"
        )
    except RPCError:
        pass


@app.on_message(filters.private & owner_filter & session_is("broadcast_waiting") & ~filters.command(_ADMIN_COMMANDS))
async def broadcast_capture(client, message: Message) -> None:
    user_id = message.from_user.id
    session_manager.clear(user_id)
    await message.reply_text("🚀 Broadcast started in the background.")
    asyncio.create_task(_run_broadcast(client, message, user_id))

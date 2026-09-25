"""
triss.handlers.batch
=====================
Owner-only. /batch starts an open-ended capture session: every message
the owner sends from the first one through /done is stored, in exact
order, and delivered as a single link. /done finalizes (idempotently —
pressing/sending it twice never creates a duplicate link) and
/cancelbatch aborts cleanly, including removing any already-copied
Store Channel messages so cancelling never leaves orphaned storage.
"""

from __future__ import annotations

import logging

from pyrogram import filters
from pyrogram.types import Message, LinkPreviewOptions
from pyrogram.errors import RPCError

from triss.bot import app
from triss.database import models as db
from triss.services.cleanup import session_manager, session_is
from triss.services.storage import store_message, message_ref, StorageError
from triss.utils.auth import owner_filter, deny_if_not_owner
from triss.utils.keyboards import cancel_only, generated_link_keyboard
from triss.utils.tokens import generate_token, build_deep_link

logger = logging.getLogger("triss.handlers.batch")

_ADMIN_COMMANDS = ["genlink", "batch", "done", "cancelbatch", "broadcast", "settings", "start", "addadmin", "removeadmin", "listadmin", "mute", "unmute", "listmute", "linkdl", "setlinkcaption"]


@app.on_message(filters.command("batch") & filters.private)
async def batch_command(client, message: Message) -> None:
    if await deny_if_not_owner(message):
        return
    session_manager.set(message.from_user.id, "batch_active", {"messages": []})
    await message.reply_text(
        "📦 Batch started. Send messages/files in order.\n"
        "Send /done when finished, or /cancelbatch to abort.",
        reply_markup=cancel_only("batch:cancel"),
    )


@app.on_message(filters.private & owner_filter & session_is("batch_active") & ~filters.command(_ADMIN_COMMANDS))
async def batch_capture(client, message: Message) -> None:
    user_id = message.from_user.id
    session = session_manager.get(user_id)
    if session is None:
        return
    try:
        stored = await store_message(client, message)
    except StorageError as e:
        await message.reply_text(f"⚠️ {e}")
        return
    index = len(session.data["messages"])
    session.data["messages"].append(message_ref(stored, index))
    session_manager.touch(user_id)


@app.on_message(filters.command("done") & filters.private & owner_filter)
async def done_command(client, message: Message) -> None:
    user_id = message.from_user.id
    session = session_manager.get(user_id)
    if session is None or session.kind != "batch_active":
        await message.reply_text("ℹ️ There is no active batch to finish.")
        return

    messages = session.data.get("messages", [])
    if not messages:
        session_manager.clear(user_id)
        await message.reply_text("⚠️ No messages were captured; batch cancelled.")
        return

    session_manager.clear(user_id)  # consume immediately so a duplicate /done can't double-create

    token = generate_token()
    await db.create_link(token, "batch", messages)

    username = getattr(client, "username", None) or (await client.get_me()).username
    link = build_deep_link(username, token)
    response_text = f"✅ Batch link generated ({len(messages)} item(s)):\n\n<code>{link}</code>"
    # Per spec, genlink/batch link creation is NOT logged to LOG_CHANNEL_ID
    # anymore - only the 6 fixed statuses in triss.services.logging_service
    # go there. The batch is safely persisted in the Store Channel +
    # MongoDB (create_link above) regardless.

    # The link and its DB record already exist by this point. Delivering
    # the response must never fail silently — log full context and fall
    # back to a plain-text send rather than leaving the owner without any
    # confirmation that the batch actually finished.
    try:
        await message.reply_text(
            response_text,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            reply_markup=generated_link_keyboard(link),
        )
    except Exception:
        logger.exception(
            "batch: failed to send final response. user_id=%s chat_id=%s token=%s link=%s items=%d",
            user_id, message.chat.id, token, link, len(messages),
        )
        try:
            await client.send_message(
                message.chat.id,
                f"✅ Batch link generated ({len(messages)} item(s)):\n\n{link}",
            )
        except Exception:
            logger.exception(
                "batch: fallback plain-text response also failed. user_id=%s chat_id=%s token=%s link=%s",
                user_id, message.chat.id, token, link,
            )


@app.on_message(filters.command("cancelbatch") & filters.private & owner_filter)
async def cancel_batch_command(client, message: Message) -> None:
    user_id = message.from_user.id
    session = session_manager.get(user_id)
    if session is None or session.kind != "batch_active":
        await message.reply_text("ℹ️ There is no active batch to cancel.")
        return

    messages = session.data.get("messages", [])
    session_manager.clear(user_id)

    # Best-effort cleanup of already-copied Store Channel messages so a
    # cancelled batch never leaves orphaned content behind.
    if messages:
        by_chat: dict[int, list[int]] = {}
        for ref in messages:
            by_chat.setdefault(ref["chat_id"], []).append(ref["message_id"])
        for chat_id, ids in by_chat.items():
            try:
                await client.delete_messages(chat_id, ids)
            except RPCError:
                logger.warning("Could not clean up %d orphaned Store Channel message(s).", len(ids))

    await message.reply_text("🧹 Batch cancelled and temporary state cleared.")

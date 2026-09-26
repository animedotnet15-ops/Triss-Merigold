"""
triss.services.delivery
========================
Delivers stored content to a requesting user. Always uses `copy_message`
so the Store Channel is never revealed (no "Forwarded from", no source
chat). Delivers multi-message batches/custom-batches in their exact
stored order. Schedules auto-delete of the *delivered copies* only —
never touches the Store Channel originals.
"""

from __future__ import annotations

import asyncio
import logging

from pyrogram import Client
from pyrogram.types import Message
from pyrogram.errors import FloodWait, RPCError

from triss.config import config
from triss.database import models as db
from triss.utils.formatting import AUTO_DELETE_NOTICE_TEXT
from triss.utils.effects import pick_category_effect_id

logger = logging.getLogger("triss.delivery")


async def _copy_one(client: Client, user_id: int, chat_id: int, message_id: int,
                     retries: int = 0, message_effect_id: int | None = None) -> Message | None:
    kwargs = dict(chat_id=user_id, from_chat_id=chat_id, message_id=message_id)
    if message_effect_id is not None:
        kwargs["message_effect_id"] = message_effect_id
    try:
        return await client.copy_message(**kwargs)
    except TypeError as e:
        # The installed Pyrogram/Kurigram build's copy_message() does not
        # accept message_effect_id at all (unlike send_*), so any attempt
        # to pass it - even a real, non-None value - raises TypeError
        # rather than being ignored. Retry once without it so message
        # effects degrade gracefully instead of breaking every delivery.
        if "message_effect_id" in kwargs and "message_effect_id" in str(e):
            logger.warning(
                "Installed copy_message() does not support message_effect_id; "
                "retrying delivery without the effect."
            )
            kwargs.pop("message_effect_id", None)
            return await client.copy_message(**kwargs)
        raise
    except FloodWait as e:
        if retries >= config.flood_wait_max_retries:
            logger.error("Giving up on delivering message %s after repeated FloodWait.", message_id)
            return None
        await asyncio.sleep(e.value)
        return await _copy_one(client, user_id, chat_id, message_id, retries=retries + 1,
                                message_effect_id=message_effect_id)
    except RPCError as e:
        # An invalid/rejected message_effect_id (e.g. an unconfirmed seed
        # id - see triss.utils.effects) also surfaces as an RPCError here,
        # not a TypeError - without this branch it would fail the WHOLE
        # delivery, not just skip the animation. Retry once without the
        # effect before giving up, same as the TypeError case above.
        if "message_effect_id" in kwargs:
            logger.warning(
                "copy_message rejected message_effect_id=%s (%s) delivering %s to %s; "
                "retrying without the effect.",
                kwargs["message_effect_id"], e, message_id, user_id,
            )
            kwargs.pop("message_effect_id", None)
            try:
                return await client.copy_message(**kwargs)
            except RPCError:
                logger.exception("Failed to deliver stored message %s to user %s.", message_id, user_id)
                return None
        logger.exception("Failed to deliver stored message %s to user %s.", message_id, user_id)
        return None


async def deliver_link_content(client: Client, user_id: int, link_doc: dict) -> list[Message]:
    """Delivers every message referenced by `link_doc` (already validated
    by the caller) in original stored order. Returns the list of delivered
    (copied, user-facing) messages so the caller can schedule auto-delete.

    Item 7: if configured, applies a Telegram message effect (🎉/🔥/etc)
    to ONLY the LAST delivered message — not every item in a batch, which
    would be spammy/repetitive - giving a single celebratory "all done!"
    moment instead."""
    delivered: list[Message] = []
    messages = sorted(link_doc.get("messages", []), key=lambda m: m.get("index", 0))
    settings = await db.get_settings()
    effect_id = pick_category_effect_id(settings, "delivery")

    for i, ref in enumerate(messages):
        is_last = i == len(messages) - 1
        msg = await _copy_one(
            client, user_id, ref["chat_id"], ref["message_id"],
            message_effect_id=effect_id if is_last else None,
        )
        if msg is not None:
            delivered.append(msg)
    return delivered


async def schedule_auto_delete(client: Client, chat_id: int, message_ids: list[int]) -> None:
    """Fire-and-forget auto-delete of bot-delivered messages, per the
    configured Auto Delete duration. Never raises into the caller —
    failures are logged and swallowed so they can't crash the bot."""
    settings = await db.get_settings()
    auto_delete = settings.get("auto_delete", {})
    if not auto_delete.get("enabled") or not message_ids:
        return
    seconds = int(auto_delete.get("seconds", 0))
    if seconds <= 0:
        return

    async def _job():
        notice = None
        if auto_delete.get("notify", True):
            try:
                notice = await client.send_message(chat_id, AUTO_DELETE_NOTICE_TEXT)
            except RPCError:
                notice = None
        await asyncio.sleep(seconds)
        try:
            await client.delete_messages(chat_id, message_ids)
        except RPCError:
            logger.warning("Auto-delete failed for chat %s (messages may already be gone).", chat_id)
        if notice is not None:
            try:
                await client.delete_messages(chat_id, [notice.id])
            except RPCError:
                pass

    asyncio.create_task(_job())


async def deliver_and_schedule(client, user_id: int, link_doc: dict) -> bool:
    """Shared helper: deliver a link's content and schedule auto-delete for
    the delivered copies. Returns True if at least one item was delivered."""
    delivered = await deliver_link_content(client, user_id, link_doc)
    if not delivered:
        return False
    await schedule_auto_delete(client, user_id, [m.id for m in delivered])
    return True


async def send_temporary(client: Client, chat_id: int, text: str, **kwargs) -> Message | None:
    """Sends a bot-generated message and, if Auto Delete is enabled,
    schedules it for deletion too (welcome/forcesub/expiry notices etc.)."""
    try:
        msg = await client.send_message(chat_id, text, **kwargs)
    except RPCError:
        logger.exception("Failed to send temporary message to %s.", chat_id)
        return None
    await schedule_auto_delete(client, chat_id, [msg.id])
    return msg



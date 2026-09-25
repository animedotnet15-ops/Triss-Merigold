"""
triss.services.storage
=======================
Handles moving owner-submitted messages into the Store Channel. Uses
`copy_message` (never `forward_message`) so the Store Channel identity,
"Forwarded from" attribution, and any other source metadata is never
attached to the copy — this is what keeps the Store Channel invisible
to end users downstream, and it also means we don't depend solely on a
raw file_id (we keep our own channel message_id as the source of truth).
"""

from __future__ import annotations

import asyncio
import logging

from pyrogram import Client
from pyrogram.types import Message
from pyrogram.errors import (
    FloodWait, RPCError,
    ChatAdminRequired, ChatWriteForbidden, ChannelPrivate,
    PeerIdInvalid, ChannelInvalid, UserNotParticipant,
)

from triss.config import config
from triss.database import models as db

logger = logging.getLogger("triss.storage")


class StorageError(Exception):
    pass


async def _get_storage_channel_id() -> int:
    settings = await db.get_settings()
    channel_id = settings.get("storage_channel_id") or config.storage_channel_id
    if not channel_id:
        raise StorageError(
            "No Store Channel configured. Ask the owner to set one via "
            "/settings -> 🏪 Store Channel."
        )
    return channel_id


async def store_message(client: Client, message: Message, retries: int = 0) -> Message:
    """Copies a single message into the Store Channel and returns the copy
    (which lives in the Store Channel, with its own message_id there)."""
    channel_id = await _get_storage_channel_id()
    try:
        copied = await message.copy(chat_id=channel_id)
        return copied
    except FloodWait as e:
        if retries >= config.flood_wait_max_retries:
            raise StorageError("Telegram FloodWait limit exceeded while storing content.") from e
        logger.warning("FloodWait %ss while storing message; retrying.", e.value)
        await asyncio.sleep(e.value)
        return await store_message(client, message, retries=retries + 1)
    except (ChatAdminRequired, ChatWriteForbidden) as e:
        logger.exception("Bot is not admin (or lacks post rights) in the Store Channel.")
        raise StorageError(
            "The bot is not an admin in the Store Channel (or is missing "
            "'Post Messages' rights). Re-add it as admin there, then try "
            "again."
        ) from e
    except (ChannelPrivate, ChannelInvalid, PeerIdInvalid, UserNotParticipant) as e:
        logger.exception("Store Channel is unreachable (deleted, ID changed, or bot not a member).")
        raise StorageError(
            "The configured Store Channel could not be reached - it may "
            "have been deleted, recreated with a new ID, or the bot was "
            "removed from it. Set a working channel via /settings -> "
            "🏪 Store Channel."
        ) from e
    except RPCError as e:
        logger.exception("Failed to store message in Store Channel.")
        raise StorageError(f"Could not store this content: {e}") from e


def message_ref(stored: Message, index: int) -> dict:
    return {
        "chat_id": stored.chat.id,
        "message_id": stored.id,
        "index": index,
    }
    

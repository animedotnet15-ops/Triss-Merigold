"""
triss.services.logging_service
===============================
Sends compact, non-sensitive event notices to LOG_CHANNEL_ID if it is
configured. Never includes secrets. Silently disabled (with a one-time
debug note) if no log channel is configured.

Item 11 — five event types, one function each, all called from their
real trigger point (see the docstring on each for exactly where):
  a) log_bot_start        - triss.handlers.start (both plain AND token starts)
  b) log_verified         - triss.handlers.start, on successful verification
  c) log_bypass_detected  - triss.handlers.start, on the BYPASS outcome
  d) log_user_muted       - triss.handlers.admin, on /mute
  e) log_link_created     - triss.handlers.genlink / triss.handlers.batch,
                             also copies the actual file(s) into the log
                             channel (not just a text notice) — this is
                             a SEPARATE copy from the Store Channel one
                             (triss.services.storage), so removing/losing
                             the log channel later never affects delivery.
"""

from __future__ import annotations

import logging
import time

from pyrogram import Client
from pyrogram.errors import RPCError
from pyrogram.types import LinkPreviewOptions, Message

from triss.config import config

logger = logging.getLogger("triss.logging_service")


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())


def _user_lines(user_id: int, username: str | None, first_name: str | None) -> list[str]:
    lines = [f"User ID: <code>{user_id}</code>"]
    if first_name:
        lines.append(f"Name: {first_name}")
    if username:
        lines.append(f"Username: @{username}")
    return lines


async def log_event(client: Client, text: str) -> None:
    if not config.log_channel_id:
        return
    try:
        await client.send_message(
            config.log_channel_id, text, link_preview_options=LinkPreviewOptions(is_disabled=True)
        )
    except RPCError:
        logger.warning("Failed to deliver log event to LOG_CHANNEL_ID.", exc_info=True)
    except Exception:
        logger.warning("Unexpected error sending log event.", exc_info=True)


# --- a) bot start -----------------------------------------------------------

async def log_bot_start(client: Client, user_id: int, username: str | None,
                         first_name: str | None, *, has_token: bool) -> None:
    kind = "with a content link" if has_token else "(plain)"
    lines = [f"🚀 <b>New /start</b> {kind}", *_user_lines(user_id, username, first_name),
              f"Time: {_timestamp()}"]
    await log_event(client, "\n".join(lines))


# --- b) verified -------------------------------------------------------------

async def log_verified(client: Client, user_id: int, username: str | None,
                        first_name: str | None, access_token: str) -> None:
    lines = [
        "✅ <b>Shortener verification completed</b>",
        *_user_lines(user_id, username, first_name),
        f"Token: <code>{access_token}</code>",
        f"Time: {_timestamp()}",
    ]
    await log_event(client, "\n".join(lines))


# --- c) bypass detected -------------------------------------------------------

async def log_bypass_detected(client: Client, user_id: int, username: str | None,
                               first_name: str | None, strike_count: int, strike_limit: int) -> None:
    muted = strike_count >= strike_limit
    lines = [
        "🚨 <b>Bypass detected</b>" + (" — muted" if muted else ""),
        *_user_lines(user_id, username, first_name),
        f"Strike: {strike_count}/{strike_limit}",
        f"Time: {_timestamp()}",
    ]
    await log_event(client, "\n".join(lines))


# --- d) muted ------------------------------------------------------------------

async def log_user_muted(client: Client, user_id: int, muted_by: int, reason: str | None) -> None:
    lines = [
        "🔇 <b>User muted</b>",
        f"User ID: <code>{user_id}</code>",
        f"Muted by: <code>{muted_by}</code>",
    ]
    if reason:
        lines.append(f"Reason: {reason}")
    lines.append(f"Time: {_timestamp()}")
    await log_event(client, "\n".join(lines))


# --- e) direct link created (+ the actual file) ---------------------------------

async def log_link_created(client: Client, creator_id: int, creator_username: str | None,
                            link: str, source_message: Message, item_count: int = 1) -> None:
    """Copies the ORIGINAL owner-submitted message (source_message) into
    the log channel — a separate copy from the one triss.services.storage
    makes into the Store Channel, so the log channel is purely an audit
    trail and never becomes a second source of truth for delivery."""
    if not config.log_channel_id:
        return
    caption_lines = [
        "🔗 <b>Link created</b>" + (f" ({item_count} item(s))" if item_count > 1 else ""),
        f"By: <code>{creator_id}</code>" + (f" (@{creator_username})" if creator_username else ""),
        f"Link: <code>{link}</code>",
        f"Time: {_timestamp()}",
    ]
    try:
        await client.copy_message(
            config.log_channel_id, source_message.chat.id, source_message.id,
            caption="\n".join(caption_lines),
        )
    except RPCError:
        # Fall back to a text-only notice (still useful) if the copy itself
        # fails (e.g. the message had no caption-able media type).
        logger.warning("Could not copy source file to log channel; sending text-only notice.", exc_info=True)
        await log_event(client, "\n".join(caption_lines))
    except Exception:
        logger.warning("Unexpected error copying to log channel.", exc_info=True)
        await log_event(client, "\n".join(caption_lines))


async def log_link_created_from_ref(client: Client, creator_id: int, creator_username: str | None,
                                     link: str, storage_chat_id: int, storage_message_id: int,
                                     item_count: int = 1) -> None:
    """Same as log_link_created, but for batch links — copies from the
    already-stored Store Channel reference (triss.services.storage.
    message_ref) instead of a live owner message, since triss.handlers.
    batch only keeps those lightweight refs, not the original Message
    objects, once /done runs. Only the first item of a batch is copied as
    a representative sample (item_count still reflects the full batch)."""
    if not config.log_channel_id:
        return
    caption_lines = [
        f"🔗 <b>Batch link created</b> ({item_count} item(s), first shown)",
        f"By: <code>{creator_id}</code>" + (f" (@{creator_username})" if creator_username else ""),
        f"Link: <code>{link}</code>",
        f"Time: {_timestamp()}",
    ]
    try:
        await client.copy_message(
            config.log_channel_id, storage_chat_id, storage_message_id,
            caption="\n".join(caption_lines),
        )
    except RPCError:
        logger.warning("Could not copy source file to log channel; sending text-only notice.", exc_info=True)
        await log_event(client, "\n".join(caption_lines))
    except Exception:
        logger.warning("Unexpected error copying to log channel.", exc_info=True)
        await log_event(client, "\n".join(caption_lines))

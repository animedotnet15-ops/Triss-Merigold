"""
triss.handlers.activity_log
=============================
Every private message a regular user (not the owner/an admin) sends to
the bot gets a one-line copy in the Log Channel immediately - a plain
activity feed, separate from the dedicated Bot Start / Verify Complete /
etc. statuses in triss.services.logging_service.

Registered at a high group number (runs AFTER every real command/session
handler - see Pyrogram's group ordering) so it never interferes with
anything; it only OBSERVES. triss.handlers.maintenance_gate's group=-1
StopPropagation during maintenance mode means this naturally stops firing
for non-owners then too, same as everything else.
"""

from __future__ import annotations

import logging

from pyrogram import filters
from pyrogram.types import Message

from triss.bot import app
from triss.utils.auth import is_owner
from triss.services.logging_service import log_user_message

logger = logging.getLogger("triss.handlers.activity_log")

_PREVIEW_LIMIT = 300


def _preview_of(message: Message) -> str:
    text = message.text or message.caption
    if text:
        text = str(text)
        return text if len(text) <= _PREVIEW_LIMIT else text[:_PREVIEW_LIMIT] + "…"
    for kind in ("photo", "video", "document", "audio", "animation", "voice", "video_note", "sticker"):
        if getattr(message, kind, None):
            return f"[{kind}]"
    return "[message]"


@app.on_message(filters.private & filters.incoming, group=10)
async def log_every_user_message(client, message: Message) -> None:
    user = message.from_user
    if user is None or is_owner(user.id):
        return
    try:
        await log_user_message(client, user.id, user.username, user.first_name, _preview_of(message))
    except Exception:
        logger.warning("activity_log: failed to log message from user %s.", user.id, exc_info=True)

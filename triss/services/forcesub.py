"""
triss.services.forcesub
========================
Verifies whether a user satisfies the configured Force Subscription
requirements before content is delivered.

Channels and groups are verified with `get_chat_member` (a genuine,
supported Telegram Bot API capability — the bot must be an admin in
those chats to call it reliably). Telegram Folders have no membership
API at all, so per spec we never pretend to verify them: a folder entry
is always treated as a resource link that is shown to the user, but it
can never itself block access.

--------------------------------------------------------------------------
JOIN REQUEST MODE
--------------------------------------------------------------------------
A Force Sub entry can be added in one of two join_mode values (see
triss.database.models.add_force_sub / triss.handlers.callbacks'
forcesub:addchannel:<mode> / forcesub:addgroup:<mode> flow):

  "normal"  - the invite link joins the user instantly (unchanged from
              before this feature existed).
  "request" - the invite link was created with creates_join_request=True,
              so Telegram shows the user a "Request to Join" screen
              instead. `_auto_approve_join_request` below approves that
              request the moment it arrives, which makes the user a real
              member immediately — so `_is_member` below (get_chat_member)
              verifies BOTH modes identically. Nothing else in this
              module needs to know which mode an entry uses.
"""

from __future__ import annotations

import logging

from pyrogram import Client
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import RPCError, UserNotParticipant
from pyrogram.types import ChatJoinRequest

from triss.bot import app
from triss.database import models as db

logger = logging.getLogger("triss.forcesub")

_JOINED_STATUSES = {
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.OWNER,
}


async def _is_member(client: Client, chat_id: int, user_id: int) -> bool:
    try:
        member = await client.get_chat_member(chat_id, user_id)
        return member.status in _JOINED_STATUSES
    except UserNotParticipant:
        return False
    except RPCError:
        # If the bot itself lacks admin rights in that chat, or the chat is
        # unreachable, we fail open on THIS ENTRY ONLY after logging — a
        # single misconfigured entry must never brick access entirely, but
        # we do surface it to the owner via logs so they can fix it.
        logger.warning("Could not verify membership in chat %s (bot may not be admin).", chat_id)
        return True


async def get_unsatisfied_requirements(client: Client, user_id: int) -> list[dict]:
    """Returns the list of Force Sub entries the user has NOT satisfied.
    Folder entries never appear here (unverifiable by design)."""
    settings = await db.get_settings()
    if not settings.get("force_sub_enabled", True):
        return []

    entries = await db.list_force_subs()
    unsatisfied = []
    for entry in entries:
        if entry["kind"] == "folder":
            continue
        if not await _is_member(client, entry["chat_id"], user_id):
            unsatisfied.append(entry)
    return unsatisfied


async def get_display_entries() -> list[dict]:
    """All entries to show as Join buttons, including informational folders."""
    return await db.list_force_subs()


@app.on_chat_join_request()
async def _auto_approve_join_request(client: Client, request: ChatJoinRequest) -> None:
    """Auto-approves join requests for any Force Sub entry configured
    with join_mode='request' (see module docstring above) — this is the
    ONLY special handling "Join Request" mode needs; everything else
    (verification, display) reuses the exact same code path as "Normal
    Join" once the user is approved. Requests for chats that are NOT a
    configured 'request'-mode Force Sub entry are left completely alone,
    in case the owner or another bot handles them separately."""
    entries = await db.list_force_subs()
    match = next(
        (e for e in entries if e.get("chat_id") == request.chat.id and e.get("join_mode") == "request"),
        None,
    )
    if match is None:
        return
    try:
        await client.approve_chat_join_request(request.chat.id, request.from_user.id)
        logger.info("Auto-approved join request: chat=%s user=%s.", request.chat.id, request.from_user.id)
    except RPCError:
        logger.warning(
            "Could not auto-approve join request for chat %s user %s (bot may not be admin).",
            request.chat.id, request.from_user.id,
        )

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
              instead. On purpose, the bot does NOT auto-approve these -
              that would make "Join Request" behave exactly like
              "Normal Join" and defeat the point of picking it. The
              request sits pending until a real admin of that chat
              approves it from Telegram's own "Join Requests" list
              (Telegram lets ANY admin of the chat do this, not just
              this bot's owner). Until approved, `_is_member` below
              (get_chat_member) correctly reports the user as not a
              member, so Force Sub keeps blocking them - exactly the
              gate-until-approved behaviour "request" mode is for.
"""

from __future__ import annotations

import logging

from pyrogram import Client
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import RPCError, UserNotParticipant

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


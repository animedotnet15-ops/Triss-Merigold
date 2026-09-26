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
JOIN REQUEST MODE — BUG FIX
--------------------------------------------------------------------------
A Force Sub entry can be added in one of two join_mode values (see
triss.database.models.add_force_sub / triss.handlers.callbacks'
forcesub:addchannel:<mode> / forcesub:addgroup:<mode> flow):

  "normal"  - the invite link joins the user instantly. Verified with
              get_chat_member, exactly as before - UNCHANGED by this fix.

  "request" - the invite link was created with creates_join_request=True,
              so Telegram shows the user a "Request to Join" screen
              instead of joining them instantly.

THE BUG: the request is genuinely still "pending" in Telegram's own
model until a human admin approves it from Telegram's native "Join
Requests" list. get_chat_member correctly reports the user as NOT a
member the entire time it's pending — that is Telegram's real, accurate
state, not a bug in get_chat_member. But the product requirement is
that submitting the request should be enough — the user must not have
to wait for a human to approve it. So "request" mode entries can NEVER
be satisfied by get_chat_member alone; something else has to record that
a request was submitted.

THE FIX: the bot does NOT approve the request (per spec — it must stay
genuinely pending in Telegram, visible to real admins in their own Join
Requests list). Instead, `_record_join_request` below listens for the
ChatJoinRequest update Telegram sends the moment a user submits a
request to a chat where the bot is admin, and stores a bare "user X
requested to join chat Y" record (triss.database.models.
record_join_request) — no approval action taken. At Verify time,
`get_unsatisfied_requirements` treats the EXISTENCE of that record
(triss.database.models.has_join_request) as satisfying a "request"-mode
entry, entirely independently of get_chat_member/actual membership
status. This is the ONLY way to implement "submitting is enough,
approval is not required" — Telegram's Bot API has no method to query
"does this user have a pending join request" on demand, so the
ChatJoinRequest event is the one and only signal, and it must be
captured when it arrives or the information is lost.
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


async def _satisfies_request_mode(client: Client, chat_id: int, user_id: int) -> bool:
    """A "request"-mode entry is satisfied by EITHER a recorded pending
    Join Request OR genuine membership (e.g. a real admin already
    approved it, or the user joined some other way) - checking the
    recorded request first avoids an extra Bot API call on the common
    path (user just submitted the request seconds ago)."""
    if await db.has_join_request(chat_id, user_id):
        return True
    return await _is_member(client, chat_id, user_id)


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
        if entry.get("join_mode") == "request":
            satisfied = await _satisfies_request_mode(client, entry["chat_id"], user_id)
        else:
            satisfied = await _is_member(client, entry["chat_id"], user_id)
        if not satisfied:
            unsatisfied.append(entry)
    return unsatisfied


async def get_display_entries() -> list[dict]:
    """All entries to show as Join buttons, including informational folders."""
    return await db.list_force_subs()


@app.on_chat_join_request()
async def _record_join_request(client: Client, request: ChatJoinRequest) -> None:
    """Records (does NOT approve) join requests for any Force Sub entry
    configured with join_mode='request' - see module docstring above.
    The request is left genuinely pending in Telegram; a real admin of
    that chat can still approve/decline it normally from Telegram's own
    UI at any time, completely independently of this bot. Requests for
    chats that are NOT a configured 'request'-mode Force Sub entry are
    left completely alone, in case the owner or another bot handles
    them separately."""
    entries = await db.list_force_subs()
    match = next(
        (e for e in entries if e.get("chat_id") == request.chat.id and e.get("join_mode") == "request"),
        None,
    )
    if match is None:
        return
    await db.record_join_request(request.chat.id, request.from_user.id)
    logger.info("Recorded pending join request (NOT approved): chat=%s user=%s.",
                request.chat.id, request.from_user.id)
  

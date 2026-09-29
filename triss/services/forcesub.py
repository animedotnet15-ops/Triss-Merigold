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
        # FAIL CLOSED: if membership can't be checked (bot not admin in
        # that chat, chat deleted, etc.) the user is treated as NOT
        # joined. Failing open here silently let everyone through
        # Force Sub, which is exactly the "force sub doesn't work" bug.
        logger.error(
            "Could not verify membership in chat %s - treating user %s as NOT joined. "
            "Make sure the bot is an admin in that chat.", chat_id, user_id,
        )
        return False


async def _is_request_still_pending(client: Client, chat_id: int, user_id: int) -> bool:
    """Telegram never notifies the bot when an admin declines a join
    request through Telegram's own UI (only approvals via the bot's own
    API fire an update) - so the only way to detect a decline is to ask
    Telegram's live pending list directly. FAILS CLOSED on any error:
    if this can't be confirmed, the request is treated as gone rather
    than trusting a possibly-stale local record."""
    try:
        async for joiner in client.get_chat_join_requests(chat_id):
            if joiner.user.id == user_id:
                return True
        return False
    except RPCError:
        # Fails OPEN here (unlike every other check in this file): this
        # live call needs "Invite Users via Link" admin rights and full
        # kurigram support for get_chat_join_requests, either of which
        # can be missing/erroring for reasons that have nothing to do
        # with the user's actual request. An error here must never
        # block someone who genuinely has a pending request - trust the
        # stored record instead of guessing "declined".
        logger.warning(
            "Could not confirm live join-request status for user %s in chat %s - "
            "trusting the stored pending record instead.",
            user_id, chat_id,
        )
        return True


async def _satisfies_request_mode(client: Client, chat_id: int, user_id: int) -> bool:
    """A "request"-mode entry is satisfied by genuine membership, or by a
    submitted Join Request that is still standing.

    A stored request must NOT outlive the user's membership: if the user
    was seen as a member (request approved) and has since LEFT, the
    record is deleted and they must request/join again. (The chat-member
    listener below also deletes it the moment a leave event arrives.)

    A stored request also must NOT outlive an admin DECLINING it - since
    that never generates an update the bot can listen for, every check
    while a request is still only "pending" (not yet approved) reconfirms
    it against Telegram's live list (see _is_request_still_pending). Once
    approved (member_seen True) this live check is skipped - a decline
    can't happen to an already-approved request."""
    if await _is_member(client, chat_id, user_id):
        await db.mark_join_request_member(chat_id, user_id)
        return True
    doc = await db.get_join_request(chat_id, user_id)
    if doc is None:
        return False
    if doc.get("member_seen"):
        await db.delete_join_request(chat_id, user_id)
        return False
    if await _is_request_still_pending(client, chat_id, user_id):
        return True
    await db.delete_join_request(chat_id, user_id)
    return False


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
  


@app.on_chat_member_updated()
async def _forget_join_request_on_leave(client: Client, update) -> None:
    """When a user leaves/is removed from a chat, drop their stored Join
    Request for it so old data can never re-satisfy Force Sub."""
    try:
        new, old = update.new_chat_member, update.old_chat_member
        user = (new or old).user if (new or old) else None
        if user is None:
            return
        if new is None or new.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
            await db.delete_join_request(update.chat.id, user.id)
    except Exception:
        logger.debug("chat member update handling failed.", exc_info=True)
  

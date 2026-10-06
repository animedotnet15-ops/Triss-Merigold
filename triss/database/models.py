"""
triss.database.models
======================
Thin repository layer. Every function here does exactly one atomic
MongoDB operation (or a small, safe sequence of them) so callers never
touch collections directly. This keeps query shape/validation in one
place and makes it easy to audit for injection / malformed-input risks.
"""

from __future__ import annotations

import time
import copy
from bson import ObjectId
from bson.errors import InvalidId
from datetime import datetime, timedelta
from typing import Any, Optional

from pymongo import ReturnDocument

from triss.database.mongodb import database, DEFAULT_SETTINGS, SETTINGS_DOC_ID


def _deep_merge_defaults(doc: dict, defaults: dict) -> dict:
    """Fill in any keys missing from `doc` using `defaults`, recursively.
    Protects against KeyErrors after new settings fields are added in an
    upgrade, without requiring a manual DB migration."""
    merged = copy.deepcopy(defaults)
    for key, value in doc.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_defaults(value, merged[key])
        else:
            merged[key] = value
    return merged


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

async def upsert_user(user_id: int, username: Optional[str], first_name: Optional[str],
                       last_name: Optional[str]) -> bool:
    """Insert or refresh a user record. Returns True if this is a brand-new user."""
    now = time.time()
    result = await database.users.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "last_seen_at": now,
            },
            "$setOnInsert": {"user_id": user_id, "joined_at": now},
        },
        upsert=True,
    )
    return result.upserted_id is not None


async def get_all_user_ids() -> list[int]:
    cursor = database.users.find({}, {"user_id": 1, "_id": 0})
    return [doc["user_id"] async for doc in cursor]


async def get_user(user_id: int) -> Optional[dict]:
    """Looks up a previously-seen user's cached username/first_name -
    used by /mute and /ban (triss.handlers.admin) to fill out the log
    channel's Name/Username fields when the command target was given as
    a bare numeric id rather than a reply, so the log entry isn't just
    blank dashes for a user we've actually seen before."""
    return await database.users.find_one({"user_id": user_id})


async def count_users() -> int:
    return await database.users.count_documents({})


async def delete_user(user_id: int) -> None:
    """Used by broadcast to drop users who have permanently blocked the bot."""
    await database.users.delete_one({"user_id": user_id})


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

async def get_settings() -> dict:
    doc = await database.settings.find_one({"_id": SETTINGS_DOC_ID})
    if doc is None:
        doc = copy.deepcopy(DEFAULT_SETTINGS)
        await database.settings.insert_one(doc)
        return doc
    return _deep_merge_defaults(doc, DEFAULT_SETTINGS)


async def update_settings(patch: dict) -> None:
    """`patch` uses dotted-path keys, e.g. {'welcome.text': '...'} for
    a targeted, atomic $set that never clobbers sibling fields."""
    if not patch:
        return
    await database.settings.update_one({"_id": SETTINGS_DOC_ID}, {"$set": patch}, upsert=True)


# ---------------------------------------------------------------------------
# Links (genlink / batch)
# ---------------------------------------------------------------------------

async def create_link(token: str, link_type: str, messages: list[dict],
                       batch_id: Optional[str] = None,
                       expires_at: Optional[float] = None) -> None:
    """
    messages: ordered list of {"chat_id": int, "message_id": int, "index": int}
    link_type: "single" | "batch"
    """
    await database.links.insert_one({
        "token": token,
        "type": link_type,
        "messages": messages,
        "batch_id": batch_id,
        "created_at": time.time(),
        "expires_at": expires_at,
        "revoked": False,
    })


async def get_link(token: str) -> Optional[dict]:
    return await database.links.find_one({"token": token})


async def revoke_link(token: str) -> bool:
    result = await database.links.update_one({"token": token}, {"$set": {"revoked": True}})
    return result.modified_count > 0


# ---------------------------------------------------------------------------
# Force Subscription entries
# ---------------------------------------------------------------------------

async def add_force_sub(kind: str, chat_id: Optional[int], title: str,
                         invite_link: Optional[str] = None,
                         join_mode: str = "normal",
                         button_text: Optional[str] = None) -> bool:
    """kind: 'channel' | 'group' | 'folder'. For 'folder', chat_id is None and
    invite_link holds the Telegram folder share link (resource link only —
    Telegram does not expose folder-membership verification).

    join_mode: 'normal' | 'request'. 'request' means invite_link was created
    with creates_join_request=True (Telegram shows the user a "Request to
    Join" flow instead of joining instantly). The bot does NOT approve
    these - triss.services.forcesub instead treats a recorded, pending
    Join Request (triss.database.models.has_join_request) as satisfying
    this entry once the user presses Verify, without needing Telegram to
    consider them an actual member. Ignored for kind='folder' (Telegram
    exposes no join-request concept for folders).

    button_text: item 2 — a custom label for this entry's Join button
    (e.g. "🎬 Movie Updates"). None uses the default "📢 Join {title}" /
    "📝 Request to Join {title}" label (see triss.utils.keyboards.
    force_sub_user_keyboard)."""
    try:
        await database.force_subs.insert_one({
            "kind": kind,
            "chat_id": chat_id,
            "title": title,
            "invite_link": invite_link,
            "join_mode": join_mode if kind != "folder" else "normal",
            "button_text": button_text,
            "added_at": time.time(),
        })
        return True
    except Exception:
        return False


async def set_force_sub_button_text(kind: str, chat_id: Optional[int], button_text: Optional[str]) -> bool:
    """Sets (or, with button_text=None, clears back to the default label)
    the custom Join button text for one Force Sub entry."""
    result = await database.force_subs.update_one(
        {"kind": kind, "chat_id": chat_id}, {"$set": {"button_text": button_text}}
    )
    return result.matched_count > 0


async def list_force_subs() -> list[dict]:
    cursor = database.force_subs.find({})
    return [doc async for doc in cursor]


async def remove_force_sub(kind: str, chat_id: Optional[int]) -> bool:
    result = await database.force_subs.delete_one({"kind": kind, "chat_id": chat_id})
    return result.deleted_count > 0


async def clear_force_subs() -> int:
    result = await database.force_subs.delete_many({})
    return result.deleted_count


# ---------------------------------------------------------------------------
# Join requests (Force Sub "Join Request" mode fix)
# ---------------------------------------------------------------------------
# Telegram's Bot API exposes no "check if user X has a pending join
# request in chat Y" query — a ChatJoinRequest update is the ONLY signal
# the bot ever gets, and it arrives once, at submission time. So the bot
# must record it here the moment it arrives; there is nothing to poll
# later. See triss.services.forcesub for how this is used at Verify time.

async def record_join_request(chat_id: int, user_id: int) -> None:
    """Upsert — safe to call again for the same (chat_id, user_id) if the
    user cancels and resubmits a request; does not need to track status
    beyond "has one ever been submitted", per spec (a submitted request is
    enough — we never wait for real approval)."""
    await database.join_requests.update_one(
        {"chat_id": chat_id, "user_id": user_id},
        {"$set": {"chat_id": chat_id, "user_id": user_id, "requested_at": time.time()}},
        upsert=True,
    )


async def has_join_request(chat_id: int, user_id: int) -> bool:
    doc = await database.join_requests.find_one({"chat_id": chat_id, "user_id": user_id})
    return doc is not None


async def get_join_request(chat_id: int, user_id: int) -> Optional[dict]:
    return await database.join_requests.find_one({"chat_id": chat_id, "user_id": user_id})


async def mark_join_request_member(chat_id: int, user_id: int) -> None:
    """The user was seen as a real member (request got approved). If they
    later leave, the stored request must NOT keep satisfying Force Sub."""
    await database.join_requests.update_one(
        {"chat_id": chat_id, "user_id": user_id}, {"$set": {"member_seen": True}},
    )


async def delete_join_request(chat_id: int, user_id: int) -> None:
    await database.join_requests.delete_one({"chat_id": chat_id, "user_id": user_id})


# ---------------------------------------------------------------------------
# Backups
# ---------------------------------------------------------------------------

async def save_backup(payload: dict) -> str:
    payload = dict(payload)
    payload["created_at"] = time.time()
    result = await database.backups.insert_one(payload)
    return str(result.inserted_id)


async def get_latest_backup() -> Optional[dict]:
    return await database.backups.find_one(sort=[("created_at", -1)])


async def delete_latest_backup() -> bool:
    latest = await get_latest_backup()
    if latest is None:
        return False
    await database.backups.delete_one({"_id": latest["_id"]})
    return True


# ---------------------------------------------------------------------------
# Shortener verification sessions
# ---------------------------------------------------------------------------
# Every access to a protected link gets its OWN session document — sessions
# are never shared between users and never reused across accesses (including
# "Try Again": that always creates a brand-new document, it never resurrects
# or mutates the old one into a fresh attempt). All timing decisions are
# made from `created_at`/`expiration`, which are server-side epoch seconds
# set once at insert time and never trusted from client input.
#
# State machine (see triss.services.shortener.SessionState for the
# canonical constants): CREATED -> VERIFIED -> CONSUMED, with
# BYPASS / EXPIRED / INVALID / FAILED as terminal dead-ends reachable from
# CREATED. Every transition below is a single atomic MongoDB
# find_one_and_update filtered on the *current* expected status, so two
# concurrent requests (double-click, duplicate update, replayed callback)
# can never both win the same transition — only one caller ever receives
# back a non-None document, and only that caller may proceed.

VERIFICATION_TTL_GRACE_SECONDS = 300  # storage-hygiene buffer only, see mongodb.py


async def create_verification_session(user_id: int, access_token: str,
                                       session_id: str, minimum_seconds: int,
                                       maximum_seconds: int,
                                       proof_hash: Optional[str] = None,
                                       mode: str = "per_link") -> dict:
    """`mode` is "per_link" (Old Method — one verification gates exactly
    this one link) or "system_access" (New Method — one verification also
    grants a time-limited unlimited-access window; see
    grant_system_access below). Recorded on the session itself so
    triss.handlers.start can decide, once a session reaches VERIFIED,
    which of the two behaviors to apply — without needing to re-read
    live settings that could have changed mid-flow."""
    now = time.time()
    retry_count = await database.verification_sessions.count_documents(
        {"user_id": user_id, "access_token": access_token}
    )
    doc = {
        "session_id": session_id,
        "user_id": user_id,
        "access_token": access_token,
        "mode": mode,
        "created_at": now,
        "minimum_time": minimum_seconds,
        "maximum_time": maximum_seconds,
        "verification_status": "created",
        "expiration": now + maximum_seconds,
        "retry_count": retry_count,
        "completed_at": None,
        "consumed_at": None,
        # Never store the raw proof — only a salted hash of it, minted at
        # session creation (verification happens entirely inside Telegram —
        # there is no separate landing-page hit to mint it from). See
        # triss/services/shortener.py module docstring.
        "proof_hash": proof_hash,
        # Extension point for a future ShortenerProvider that genuinely
        # supports completion verification: a provider-issued reference
        # (tracking id, callback token, etc) it could use to look up or
        # validate this specific session's completion. Unused by the
        # current time-window-gating flow. Never treated as proof by
        # itself — see ShortenerProvider.verify_completion() in
        # triss/services/shortener.py.
        "provider_ref": None,
        "ttl_at": datetime.utcnow() + timedelta(seconds=maximum_seconds + VERIFICATION_TTL_GRACE_SECONDS),
    }
    await database.verification_sessions.insert_one(doc)
    return doc


async def get_verification_session(session_id: str) -> Optional[dict]:
    return await database.verification_sessions.find_one({"session_id": session_id})


async def transition_verification_session(session_id: str, from_states: list[str], to_state: str,
                                           extra_set: Optional[dict[str, Any]] = None) -> Optional[dict]:
    """
    Atomically moves a session from one of `from_states` to `to_state`.
    Returns the *updated* document only if the transition actually
    happened (i.e. the session was still in one of `from_states` at the
    moment of the update); returns None otherwise. Callers MUST treat a
    None result as "someone else already resolved this session" and must
    not deliver/act as if the transition succeeded.
    """
    patch: dict[str, Any] = {"verification_status": to_state}
    if extra_set:
        patch.update(extra_set)
    return await database.verification_sessions.find_one_and_update(
        {"session_id": session_id, "verification_status": {"$in": from_states}},
        {"$set": patch},
        return_document=ReturnDocument.AFTER,
    )


async def set_verification_status(session_id: str, status: str,
                                   completed_at: Optional[float] = None) -> None:
    """Unconditional status set — used only for terminal/failure states
    where no concurrency race matters (e.g. flagging BYPASS). Anything
    that grants access (VERIFIED, CONSUMED) MUST go through
    `transition_verification_session` instead, never through this."""
    patch: dict[str, Any] = {"verification_status": status}
    if completed_at is not None:
        patch["completed_at"] = completed_at
    await database.verification_sessions.update_one({"session_id": session_id}, {"$set": patch})


# ---------------------------------------------------------------------------
# System Access (New Method) — per-user unlimited-access window
# ---------------------------------------------------------------------------
# One successful System Access verification (see triss.services.shortener,
# mode="system_access") grants the user unlimited use of every link in the
# bot until `system_access_until` (a server-side epoch, never client-
# supplied). Stored on the user's own document since it's a per-user,
# bot-wide grant — not tied to any single link/session.

async def grant_system_access(user_id: int, until: float) -> None:
    await database.users.update_one(
        {"user_id": user_id},
        {"$set": {"system_access_until": until}},
        upsert=True,
    )


async def get_system_access_until(user_id: int) -> Optional[float]:
    doc = await database.users.find_one({"user_id": user_id}, {"system_access_until": 1})
    return doc.get("system_access_until") if doc else None


async def has_active_system_access(user_id: int) -> bool:
    until = await get_system_access_until(user_id)
    return until is not None and until > time.time()


# ---------------------------------------------------------------------------
# 🔓 rt_save (Restricted Content Save) - per-user verified window, exact
# same pattern as System Access above, just its own field/feature.
# ---------------------------------------------------------------------------

async def grant_rt_save_access(user_id: int, until: float) -> None:
    await database.users.update_one(
        {"user_id": user_id},
        {"$set": {"rt_save_access_until": until}},
        upsert=True,
    )


async def get_rt_save_access_until(user_id: int) -> Optional[float]:
    doc = await database.users.find_one({"user_id": user_id}, {"rt_save_access_until": 1})
    return doc.get("rt_save_access_until") if doc else None


async def has_active_rt_save_access(user_id: int) -> bool:
    until = await get_rt_save_access_until(user_id)
    return until is not None and until > time.time()


# ---------------------------------------------------------------------------
# Admins (item 9) — a second trust tier alongside the single hardcoded
# OWNER_ID. See triss.utils.auth for how these are checked/cached.
# ---------------------------------------------------------------------------

async def add_admin(user_id: int, added_by: int) -> bool:
    """Returns False if user_id is already an admin (no duplicate added)."""
    try:
        await database.admins.insert_one({
            "user_id": user_id,
            "added_by": added_by,
            "added_at": time.time(),
        })
        return True
    except Exception:
        return False


async def remove_admin(user_id: int) -> bool:
    result = await database.admins.delete_one({"user_id": user_id})
    return result.deleted_count > 0


async def list_admins() -> list[dict]:
    return await database.admins.find().sort("added_at", 1).to_list(length=None)


async def get_admin_ids() -> set[int]:
    """Used once at startup (triss.bot.startup) to warm the in-memory
    cache in triss.utils.auth — see that module for why the cache
    exists instead of hitting MongoDB on every permission check."""
    docs = await database.admins.find({}, {"user_id": 1}).to_list(length=None)
    return {doc["user_id"] for doc in docs}


# ---------------------------------------------------------------------------
# Mutes (item 10) — blocks a user from redeeming links / using the bot,
# independent of the Shortener anti-bypass temporary mute (which is a
# separate, in-memory, self-expiring mechanism — see triss.services.
# shortener.is_rate_limited). This mute is owner/admin-controlled,
# explicit, and persists until explicitly lifted with /unmute.
# ---------------------------------------------------------------------------

async def mute_user(user_id: int, muted_by: int, reason: Optional[str] = None) -> bool:
    """Upsert — safe to call again on an already-muted user (e.g. to
    update the reason) without erroring."""
    try:
        await database.mutes.update_one(
            {"user_id": user_id},
            {"$set": {
                "user_id": user_id,
                "muted_by": muted_by,
                "reason": reason,
                "muted_at": time.time(),
            }},
            upsert=True,
        )
        return True
    except Exception:
        return False


async def unmute_user(user_id: int) -> bool:
    result = await database.mutes.delete_one({"user_id": user_id})
    return result.deleted_count > 0


async def is_muted(user_id: int) -> bool:
    doc = await database.mutes.find_one({"user_id": user_id})
    return doc is not None


async def list_muted() -> list[dict]:
    return await database.mutes.find().sort("muted_at", 1).to_list(length=None)


# ---------------------------------------------------------------------------
# Bans — same shape as Mutes above, but a full block: a banned user is
# rejected at the very top of /start (triss.handlers.start.start_command),
# before even the plain welcome message, whereas a mute only blocks link
# redemption. Owner/admin-controlled via /ban /unban /listban
# (triss.handlers.admin), gated by deny_if_not_owner like mute.
# ---------------------------------------------------------------------------

async def ban_user(user_id: int, banned_by: int, reason: Optional[str] = None) -> bool:
    """Upsert — safe to call again on an already-banned user (e.g. to
    update the reason) without erroring."""
    try:
        await database.bans.update_one(
            {"user_id": user_id},
            {"$set": {
                "user_id": user_id,
                "banned_by": banned_by,
                "reason": reason,
                "banned_at": time.time(),
            }},
            upsert=True,
        )
        return True
    except Exception:
        return False


async def unban_user(user_id: int) -> bool:
    result = await database.bans.delete_one({"user_id": user_id})
    return result.deleted_count > 0


async def is_banned(user_id: int) -> bool:
    doc = await database.bans.find_one({"user_id": user_id})
    return doc is not None


async def list_banned() -> list[dict]:
    return await database.bans.find().sort("banned_at", 1).to_list(length=None)


# ---------------------------------------------------------------------------
# Store / Log channels (add + list + pick the active one)
# ---------------------------------------------------------------------------
# kind is "storage" or "log". The ACTIVE channel is a plain setting
# (storage_channel_id / log_channel_id) so every existing reader of those
# settings keeps working unchanged; the `channels` collection just holds
# the owner's saved list to pick from.

_ACTIVE_KEY = {"storage": "storage_channel_id", "log": "log_channel_id"}


async def add_channel(kind: str, chat_id: int, title: str) -> bool:
    """Returns False if that channel is already in the list."""
    try:
        await database.channels.insert_one({
            "kind": kind, "chat_id": chat_id, "title": title, "added_at": time.time(),
        })
        return True
    except Exception:
        return False


async def list_channels(kind: str) -> list[dict]:
    return await database.channels.find({"kind": kind}).sort("added_at", 1).to_list(length=None)


async def remove_channel(kind: str, chat_id: int) -> bool:
    result = await database.channels.delete_one({"kind": kind, "chat_id": chat_id})
    return result.deleted_count > 0


async def get_active_channel_id(kind: str) -> Optional[int]:
    from triss.config import config
    settings = await get_settings()
    fallback = config.storage_channel_id if kind == "storage" else config.log_channel_id
    return settings.get(_ACTIVE_KEY[kind]) or fallback


async def set_active_channel(kind: str, chat_id: Optional[int]) -> None:
    await update_settings({_ACTIVE_KEY[kind]: chat_id})


# ---------------------------------------------------------------------------
# Pending access (link a user was opening when Force Sub stopped them)
# ---------------------------------------------------------------------------

async def set_pending_access(user_id: int, token: str) -> None:
    await database.pending_access.update_one(
        {"user_id": user_id},
        {"$set": {"user_id": user_id, "token": token, "created_at": datetime.utcnow()}},
        upsert=True,
    )


async def pop_pending_access(user_id: int) -> Optional[str]:
    doc = await database.pending_access.find_one_and_delete({"user_id": user_id})
    return doc.get("token") if doc else None


# ---------------------------------------------------------------------------
# 💎 Premium (manual UPI payment, owner-approved via screenshot)
# ---------------------------------------------------------------------------

BRONZE_DAILY_LIMIT = 7  # fallback only - the real limit is premium.plans.bronze.daily_limit (owner-editable)


def _ist_date_str() -> str:
    return (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


async def grant_premium(user_id: int, plan: str, days: int) -> datetime:
    """Upsert - approving a fresh payment while premium is still active
    extends from NOW (not stacked on top of the old expiry), and always
    resets the Bronze daily counter so a freshly (re)approved user starts
    with a full day's quota."""
    expires_at = datetime.utcnow() + timedelta(days=days)
    await database.premium_users.update_one(
        {"user_id": user_id},
        {"$set": {
            "user_id": user_id, "plan": plan, "expires_at": expires_at,
            "granted_at": datetime.utcnow(), "daily_date": _ist_date_str(), "daily_count": 0,
        }},
        upsert=True,
    )
    return expires_at


async def get_premium(user_id: int) -> Optional[dict]:
    """None if the user has no premium, or it has expired."""
    doc = await database.premium_users.find_one({"user_id": user_id})
    if doc is None:
        return None
    expires_at = doc.get("expires_at")
    if expires_at is None or expires_at <= datetime.utcnow():
        return None
    return doc


async def revoke_premium(user_id: int) -> bool:
    result = await database.premium_users.delete_one({"user_id": user_id})
    return result.deleted_count > 0


async def list_premium_users() -> list[dict]:
    """Only currently-active (non-expired) premium users, soonest-to-expire
    first. An already-expired doc just hasn't been cleaned up yet - never
    shown as active (matches get_premium's own expiry check)."""
    docs = await database.premium_users.find({"expires_at": {"$gt": datetime.utcnow()}}) \
        .sort("expires_at", 1).to_list(length=None)
    return docs


async def get_bronze_daily_count(user_id: int) -> int:
    """Today's (IST) delivery count - 0 if nothing used yet today, and
    automatically treated as 0 the moment the stored date rolls over."""
    doc = await database.premium_users.find_one({"user_id": user_id})
    if doc is None or doc.get("daily_date") != _ist_date_str():
        return 0
    return int(doc.get("daily_count") or 0)


async def increment_bronze_daily_count(user_id: int) -> int:
    """Call once per successful delivery for a Bronze user. Returns the
    new count. Resets to 1 automatically on a new IST day."""
    today = _ist_date_str()
    doc = await database.premium_users.find_one({"user_id": user_id})
    if doc is None:
        return 0  # shouldn't happen - caller already confirmed premium
    if doc.get("daily_date") != today:
        await database.premium_users.update_one(
            {"user_id": user_id}, {"$set": {"daily_date": today, "daily_count": 1}},
        )
        return 1
    await database.premium_users.update_one(
        {"user_id": user_id}, {"$inc": {"daily_count": 1}},
    )
    return int(doc.get("daily_count") or 0) + 1


async def create_premium_request(user_id: int, plan: str, screenshot_chat_id: int,
                                  screenshot_message_id: int) -> str:
    result = await database.premium_requests.insert_one({
        "user_id": user_id, "plan": plan,
        "screenshot_chat_id": screenshot_chat_id, "screenshot_message_id": screenshot_message_id,
        "status": "pending", "created_at": datetime.utcnow(),
    })
    return str(result.inserted_id)


async def get_premium_request(request_id: str) -> Optional[dict]:
    try:
        oid = ObjectId(request_id)
    except InvalidId:
        return None
    return await database.premium_requests.find_one({"_id": oid})


async def set_premium_request_status(request_id: str, status: str) -> None:
    try:
        oid = ObjectId(request_id)
    except InvalidId:
        return
    await database.premium_requests.update_one({"_id": oid}, {"$set": {"status": status}})



# ---------------------------------------------------------------------------
# 🔍 Group filters (keyword -> stored content, triggered in connected groups)
# ---------------------------------------------------------------------------

async def add_filter(keyword: str, chat_id: int, message_id: int, added_by: int) -> bool:
    """Returns False if that keyword (case-insensitive) already exists."""
    try:
        await database.keyword_filters.insert_one({
            "keyword": keyword.lower(), "display": keyword,
            "chat_id": chat_id, "message_id": message_id,
            "added_by": added_by, "created_at": time.time(),
        })
        return True
    except Exception:
        return False


async def list_filters() -> list[dict]:
    return await database.keyword_filters.find().sort("display", 1).to_list(length=None)


async def remove_filter(keyword: str) -> bool:
    result = await database.keyword_filters.delete_one({"keyword": keyword.lower()})
    return result.deleted_count > 0


async def find_matching_filters(text: str) -> list[dict]:
    """Every saved filter whose keyword is a case-insensitive substring of
    `text` - the actual matching happens in Python (not a Mongo query)
    since the filter list is expected to stay small; simplest and most
    predictable for a "contains" match in either direction."""
    text_lower = text.lower()
    matches = []
    async for doc in database.keyword_filters.find():
        if doc["keyword"] in text_lower:
            matches.append(doc)
    return matches

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
    Join" flow instead of joining instantly) - triss.services.forcesub's
    chat-join-request handler auto-approves these immediately, so
    verification (get_chat_member) works identically either way. Ignored
    for kind='folder' (Telegram exposes no join-request concept for folders).

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
                                       proof_hash: Optional[str] = None) -> dict:
    now = time.time()
    retry_count = await database.verification_sessions.count_documents(
        {"user_id": user_id, "access_token": access_token}
    )
    doc = {
        "session_id": session_id,
        "user_id": user_id,
        "access_token": access_token,
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


                                       

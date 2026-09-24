"""
triss.utils.auth
=================
Authorization for the bot's two trust tiers:

  Owner  - the single, hardcoded OWNER_ID from config/env. Always
           trusted, cannot be removed, and is the ONLY identity that can
           manage admins (is_super_owner / deny_if_not_super_owner).
  Admin  - zero or more users added at runtime via /addadmin (see
           triss.handlers.admin). Admins are intentionally given the
           SAME access as the owner for every existing owner-only
           action across the bot (settings, broadcast, force sub,
           genlink, batch, etc.) - `is_owner()` / `deny_if_not_owner()`
           check BOTH tiers together, on purpose, so the ~30 existing
           call sites across the codebase did not each need to be
           individually widened. The one thing admins can never do is
           manage other admins - that is gated separately below.

Never authorize based on username, first/last name, or unvalidated
callback data - always the numeric user_id Pyrogram attaches to the
update, checked against config.owner_id or the admin id cache below.

--------------------------------------------------------------------------
ADMIN CACHE
--------------------------------------------------------------------------
Admin ids live in MongoDB (triss.database.models add_admin/remove_admin/
list_admin_ids) but are also mirrored into an in-memory set here so every
owner-only check (many of which are plain, synchronous pyrogram filters
evaluated on every single update) stays fast and doesn't need to be
rewritten as async. triss.bot's startup() populates this cache once at
boot; triss.handlers.admin updates it immediately on every
/addadmin /removeadmin so changes take effect without a restart.
"""

from __future__ import annotations

from pyrogram import filters
from pyrogram.types import Message, CallbackQuery

from triss.config import config

_admin_ids_cache: set[int] = set()


def set_admin_cache(admin_ids) -> None:
    """Replaces the whole in-memory admin id cache — called once at
    startup (triss.bot.startup) and again after every /addadmin or
    /removeadmin (triss.handlers.admin) so changes are live immediately."""
    global _admin_ids_cache
    _admin_ids_cache = set(admin_ids)


def get_admin_cache() -> set[int]:
    return set(_admin_ids_cache)


def is_owner(user_id: int | None) -> bool:
    """True for the configured OWNER_ID OR any admin added via
    /addadmin - see module docstring for why admins are folded into
    this same check rather than threaded through every call site
    separately."""
    if user_id is None:
        return False
    return user_id == config.owner_id or user_id in _admin_ids_cache


def is_super_owner(user_id: int | None) -> bool:
    """True ONLY for the configured OWNER_ID - never true for an admin,
    however trusted. Used exclusively to gate /addadmin, /removeadmin
    and /listadmin, so an admin can never promote/demote themselves or
    anyone else."""
    return user_id is not None and user_id == config.owner_id


async def _owner_or_admin_filter_func(_, __, update) -> bool:
    user = update.from_user
    return user is not None and is_owner(user.id)


# Dynamic filter (re-evaluates the live admin cache on every update) -
# a plain filters.user(config.owner_id) can't reflect admins added at
# runtime, so this uses filters.create() instead.
owner_filter = filters.create(_owner_or_admin_filter_func)


async def deny_if_not_owner(update: Message | CallbackQuery) -> bool:
    """Returns True (and answers/replies) if the update should be denied.
    Passes for the owner OR any admin - see module docstring."""
    user = update.from_user
    if user is None or not is_owner(user.id):
        if isinstance(update, CallbackQuery):
            await update.answer("🚫 Owner only.", show_alert=True)
        else:
            await update.reply_text("🚫 This command is restricted to the bot owner.")
        return True
    return False


async def deny_if_not_super_owner(update: Message | CallbackQuery) -> bool:
    """Stricter than deny_if_not_owner: passes ONLY for the real
    OWNER_ID, never for an admin. Use this (not deny_if_not_owner) for
    any action that manages the admin list itself."""
    user = update.from_user
    if user is None or not is_super_owner(user.id):
        if isinstance(update, CallbackQuery):
            await update.answer("🚫 Owner only — admins can't manage other admins.", show_alert=True)
        else:
            await update.reply_text("🚫 This command is restricted to the bot owner only (not admins).")
        return True
    return False

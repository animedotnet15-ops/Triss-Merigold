"""
triss.utils.restrict
======================
"Restrict Content" — when ON, EVERY message this bot sends (welcome,
force sub prompts, shortener popups, broadcasts, delivered files,
admin/settings replies, everything) is sent with Telegram's own
`protect_content=True`, which disables forwarding and saving of that
message for every recipient, including the owner's own chat.

WHY THIS IS A PATCH ON pyrogram.Client, NOT ~170 EDITED CALL SITES
--------------------------------------------------------------------------
This codebase calls `message.reply_text(...)` (152 call sites),
`client.send_photo(...)`, `client.copy_message(...)`, etc. all over the
bot. Pyrogram's `Message.reply_*` convenience methods are thin wrappers
that internally call the matching `Client.send_*` method on
`self._client` — so patching the six `Client` methods actually used in
this codebase, ONCE, here, makes `protect_content` apply everywhere
those wrappers are used too, automatically, with zero changes to any
existing handler file. This is patched at the CLASS level
(`pyrogram.Client`), which is safe specifically because this bot creates
exactly one `Client` instance for its whole lifetime (see triss.bot).

The in-memory cache (`set_restrict_content_cache` / get) mirrors the
same pattern already used for the admin id cache in triss.utils.auth:
warmed once from MongoDB at startup, updated immediately whenever the
owner flips the setting, so every send is a synchronous dict/bool read —
never a MongoDB round-trip in the hot path.
"""

from __future__ import annotations

import functools
import logging

from pyrogram import Client

logger = logging.getLogger("triss.restrict")

_restrict_enabled_cache: bool = False
_log_channel_id_cache: int | None = None
_installed = False

# Only the Client methods actually invoked (directly or via a
# message.reply_*/copy wrapper) anywhere in this codebase today. Add a
# name here if a future call site starts using a different send method.
_PATCHED_METHODS = (
    "send_message",
    "send_photo",
    "send_video",
    "send_animation",
    "send_sticker",
    "copy_message",
)


def set_restrict_content_cache(value: bool) -> None:
    """Called once at startup (triss.bot.startup) to warm the cache from
    MongoDB, and again immediately after every settings:restrict toggle
    (triss.handlers.callbacks) so the change is live with no restart."""
    global _restrict_enabled_cache
    _restrict_enabled_cache = bool(value)


def get_restrict_content_cache() -> bool:
    return _restrict_enabled_cache


def set_log_channel_id_cache(chat_id: int | None) -> None:
    """Called once at startup and again the instant the owner changes the
    active Log Channel (triss.handlers.callbacks' chan:log:* actions), so
    logging_service/linkdl's sends INTO the Log Channel are never
    protect_content'd - that channel is the owner's own audit trail, not
    "content" being redistributed, and messages there must stay
    forwardable regardless of the Restrict Content setting."""
    global _log_channel_id_cache
    _log_channel_id_cache = chat_id


def _destination_chat_id(args, kwargs) -> object:
    if "chat_id" in kwargs:
        return kwargs["chat_id"]
    return args[0] if args else None


def _wrap(original, name: str):
    @functools.wraps(original)
    async def wrapper(self, *args, **kwargs):
        is_log_channel = (
            _log_channel_id_cache is not None
            and _destination_chat_id(args, kwargs) == _log_channel_id_cache
        )
        if _restrict_enabled_cache and not is_log_channel and "protect_content" not in kwargs:
            kwargs["protect_content"] = True
        try:
            return await original(self, *args, **kwargs)
        except TypeError as e:
            # Defensive fallback, same pattern this codebase already uses
            # elsewhere for optional kwargs: if the installed Pyrogram/
            # Kurigram build's version of this method doesn't accept
            # protect_content at all, retry once without it instead of
            # breaking every single send while Restrict Content is on.
            if "protect_content" in kwargs and "protect_content" in str(e):
                logger.warning(
                    "%s on this build doesn't accept protect_content; retrying without it.", name
                )
                kwargs.pop("protect_content", None)
                return await original(self, *args, **kwargs)
            raise
    return wrapper


def install_restrict_content_patch() -> None:
    """Idempotent — safe to call more than once (only patches each method
    the first time). Must run before the bot starts handling updates;
    called once at import time by triss.bot, right after the Client is
    constructed."""
    global _installed
    if _installed:
        return
    for name in _PATCHED_METHODS:
        original = getattr(Client, name, None)
        if original is None:
            logger.warning(
                "pyrogram.Client.%s not found on this build; Restrict Content "
                "will not apply to it.", name,
            )
            continue
        setattr(Client, name, _wrap(original, name))
    _installed = True
    logger.info("Restrict Content patch installed on: %s", ", ".join(_PATCHED_METHODS))

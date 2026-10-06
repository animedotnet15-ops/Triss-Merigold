"""
triss.handlers.maintenance_gate
================================
BUG FIX: "Maintenance on/off doesn't work."

Before this file, "maintenance" was only ever checked inside
triss.handlers.start's /start command - every other command
(/genlink, /batch, /settings, /search, /help, /rt_save, group filters,
premium, ...) and every callback query (shortener verify, force-sub
verify, settings menus the owner left open, ...) kept working normally
for everyone while maintenance was "on". That is not what Maintenance
Mode means - it is supposed to mean the bot is owner-only until turned
back off.

This module is the single, central fix: registered in Pyrogram handler
group -1 (every other handler in this codebase is in the default group
0, and Pyrogram always processes lower-numbered groups first, for every
update), it runs before anything else ever can. The owner (and any
/addadmin admin - see triss.utils.auth.is_owner) always passes straight
through untouched, so toggling maintenance back off is never blocked.
Anyone else, while maintenance is on, never reaches ANY other handler
at all - `pyrogram.StopPropagation` stops group 0 from running for that
update entirely.

The maintenance flag itself is read from an in-memory cache
(triss.utils.maintenance), warmed at startup and updated the instant
the owner toggles it (triss.handlers.callbacks' maintenance_router) -
exactly the same pattern already used for Restrict Content and the
admin cache, so this check costs nothing beyond a dict/bool read on
every single incoming update, even while maintenance is off.
"""

from __future__ import annotations

import logging

import pyrogram
from pyrogram import filters
from pyrogram.enums import ChatType
from pyrogram.types import CallbackQuery, Message

from triss.bot import app
from triss.utils.auth import is_owner
from triss.utils.formatting import MAINTENANCE_TEXT
from triss.utils.maintenance import get_maintenance_cache

logger = logging.getLogger("triss.maintenance_gate")


@app.on_message(filters.all, group=-1)
async def _maintenance_gate_message(client, message: Message) -> None:
    user = message.from_user
    if user is None:
        return  # channel posts / anonymous admins - nothing to gate here
    if is_owner(user.id):
        return
    if not get_maintenance_cache():
        return

    if message.chat is not None and message.chat.type == ChatType.PRIVATE:
        try:
            await message.reply_text(MAINTENANCE_TEXT)
        except Exception:
            logger.debug("Could not send maintenance notice.", exc_info=True)
    raise pyrogram.StopPropagation


@app.on_callback_query(filters.all, group=-1)
async def _maintenance_gate_callback(client, cq: CallbackQuery) -> None:
    user = cq.from_user
    if user is None or is_owner(user.id):
        return
    if not get_maintenance_cache():
        return

    try:
        await cq.answer("🧑‍🔧 Bot is under maintenance. Please try again later.", show_alert=True)
    except Exception:
        logger.debug("Could not answer callback with maintenance notice.", exc_info=True)
    raise pyrogram.StopPropagation

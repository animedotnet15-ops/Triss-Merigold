"""
triss.handlers.groupfilters
=============================
🔍 Group Filters — keyword-triggered auto-replies, group-only.

Creating a filter (owner, in PM):
    /search  -> bot asks for the content (any message: text/photo/video/
                document/etc.) -> owner sends it -> bot asks for the
                keyword/name -> owner sends e.g. "Naruto" -> saved.
    The content is copied into the Store Channel (same as genlink/batch);
    only the keyword + a {chat_id, message_id} reference go in MongoDB.

Triggering (any group the bot is in, but ONLY if that group has been
Connected via /settings -> 👥 Group Settings -> 🔗 Connect):
    Any message whose text/caption CONTAINS a saved keyword
    (case-insensitive substring, e.g. "I love Naruto" matches "Naruto")
    gets that filter's content copied in as a reply. ALL matching
    filters fire, not just the first (per owner's explicit choice).

Settings -> 👥 Group Settings:
    🔗 Connect  — Add / Remove / List which groups filters are active in.
    🔍 Filter   — List / Remove saved filters (adding is via /search only).
"""

from __future__ import annotations

import asyncio
import logging

from pyrogram import filters
from pyrogram.types import CallbackQuery, Message

from triss.bot import app
from triss.database import models as db
from triss.services.cleanup import session_manager, session_is
from triss.services.storage import store_message, StorageError
from triss.utils.auth import owner_filter, deny_if_not_owner
from triss.utils.keyboards import (
    cancel_only, group_settings_menu, group_connect_menu, group_connect_list_menu,
    filter_settings_menu, filter_remove_menu,
)

logger = logging.getLogger("triss.handlers.groupfilters")

_COMMANDS = [
    "genlink", "batch", "done", "cancelbatch", "autobatch", "broadcast", "settings", "start",
    "addadmin", "removeadmin", "listadmin", "mute", "unmute", "listmute",
    "ban", "unban", "listban", "linkdl", "setlinkcaption", "buy_premium",
    "setvip", "listvip", "deletevip", "search", "rt_save", "help",
]


# ---------------------------------------------------------------------------
# a) /search -> content -> keyword (owner, PM only)
# ---------------------------------------------------------------------------

@app.on_message(filters.command("search") & filters.private)
async def search_cmd(client, message: Message) -> None:
    if await deny_if_not_owner(message):
        return
    session_manager.set(message.from_user.id, "filter_awaiting_content")
    await message.reply_text(
        "🔍 <b>New Filter</b>\n\nSend the content to save now — text, photo, video, document, "
        "anything.",
        reply_markup=cancel_only("generic:cancel"),
    )


@app.on_message(filters.private & owner_filter & session_is("filter_awaiting_content")
                 & ~filters.command(_COMMANDS))
async def capture_filter_content(client, message: Message) -> None:
    try:
        stored = await store_message(client, message)
    except StorageError as e:
        await message.reply_text(f"⚠️ {e}")
        return
    session_manager.set(message.from_user.id, "filter_awaiting_keyword",
                        {"chat_id": stored.chat.id, "message_id": stored.id})
    await message.reply_text(
        "✅ Saved. Now send the <b>keyword/name</b> for this filter (e.g. <code>Naruto</code>)."
    )


@app.on_message(filters.private & owner_filter & session_is("filter_awaiting_keyword")
                 & filters.text & ~filters.command(_COMMANDS))
async def capture_filter_keyword(client, message: Message) -> None:
    session = session_manager.get(message.from_user.id)
    data = session.data if session else {}
    chat_id, stored_message_id = data.get("chat_id"), data.get("message_id")
    session_manager.clear(message.from_user.id)
    if chat_id is None or stored_message_id is None:
        await message.reply_text("⚠️ That filter session expired - run /search again.")
        return
    keyword = message.text.strip()
    if not keyword:
        await message.reply_text("⚠️ Send a non-empty keyword.")
        return
    added = await db.add_filter(keyword, chat_id, stored_message_id, message.from_user.id)
    if added:
        await message.reply_text(f"✅ Filter <b>{keyword}</b> saved.")
    else:
        await message.reply_text(f"⚠️ A filter named <b>{keyword}</b> already exists.")


# ---------------------------------------------------------------------------
# b) group trigger - ONLY in Connected groups
# ---------------------------------------------------------------------------

_LOADING_STEPS = 10
_LOADING_SECONDS = 8  # total animation time - the post(s) land right after


async def _show_loading_bar(message: Message) -> Message | None:
    """0% -> 100% over _LOADING_SECONDS, _LOADING_STEPS edits of the same
    message (cheap - well under Telegram's edit rate limits). Returns the
    loading message so the caller can delete it once the real post(s)
    are ready, or None if even the first send failed."""
    try:
        loading = await message.reply_text(f"🔍Search: [{'□' * _LOADING_STEPS}] 0.0%")
    except Exception:
        logger.warning("groupfilters: could not send loading animation.", exc_info=True)
        return None
    step_seconds = _LOADING_SECONDS / _LOADING_STEPS
    for i in range(1, _LOADING_STEPS + 1):
        await asyncio.sleep(step_seconds)
        pct = i * 100 / _LOADING_STEPS
        bar = "■" * i + "□" * (_LOADING_STEPS - i)
        try:
            await loading.edit_text(f"🔍Search: [{bar}] {pct:.1f}%")
        except Exception:
            pass  # a single dropped edit shouldn't abort the animation
    return loading


@app.on_message(filters.group & (filters.text | filters.caption) & ~filters.via_bot & ~filters.me)
async def group_filter_trigger(client, message: Message) -> None:
    """~filters.me stops the bot's own filter-reply from re-scanning and
    potentially cascading into more filter replies."""
    connected = await db.list_channels("group")
    if not any(g["chat_id"] == message.chat.id for g in connected):
        return
    text = message.text or message.caption or ""
    if not text:
        return
    matches = await db.find_matching_filters(text)
    if not matches:
        return

    loading = await _show_loading_bar(message)

    for f in matches:
        try:
            await client.copy_message(
                chat_id=message.chat.id, from_chat_id=f["chat_id"], message_id=f["message_id"],
                reply_to_message_id=message.id,
            )
        except Exception:
            logger.warning("groupfilters: could not deliver filter '%s' in chat %s.",
                           f.get("display"), message.chat.id, exc_info=True)

    if loading is not None:
        try:
            await loading.delete()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# c) settings: Connect (Add/Remove/List)
# ---------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^groupset:"))
async def group_settings_router(client, cq: CallbackQuery) -> None:
    if await deny_if_not_owner(cq):
        return
    action = cq.data.split(":")[1]
    if action == "connect":
        await cq.message.edit_text("🔗 <b>Connect</b>\n\nGroups where 🔍 Filter auto-replies are active.",
                                    reply_markup=group_connect_menu())
    elif action == "filter":
        count = len(await db.list_filters())
        await cq.message.edit_text(f"🔍 <b>Filter</b>\n\n{count} saved. Add new ones via /search in PM.",
                                    reply_markup=filter_settings_menu())
    await cq.answer()


@app.on_callback_query(filters.regex(r"^groupconn:"))
async def group_connect_router(client, cq: CallbackQuery) -> None:
    if await deny_if_not_owner(cq):
        return
    parts = cq.data.split(":")
    action = parts[1]
    user_id = cq.from_user.id

    if action == "add":
        session_manager.set(user_id, "groupfilter_connect_add")
        await cq.message.reply_text(
            "🔗 Forward any message from the group, or send its numeric ID. The bot must be an admin there.",
            reply_markup=cancel_only("generic:cancel"),
        )
        await cq.answer()
        return
    if action == "rm" and len(parts) > 2:
        await db.remove_channel("group", int(parts[2]))
        await cq.answer("Removed.")
    groups = await db.list_channels("group")
    await cq.message.edit_text(
        f"🔗 <b>Connect</b>\n\n{len(groups)} group(s) connected. Tap to remove.",
        reply_markup=group_connect_list_menu(groups),
    )
    if action != "rm":
        await cq.answer()


@app.on_message(filters.private & owner_filter & session_is("groupfilter_connect_add")
                 & ~filters.command(_COMMANDS))
async def capture_group_connect(client, message: Message) -> None:
    from triss.handlers.callbacks import _resolve_chat_from_message, _bot_admin_status, _ADMIN_STATUSES

    chat_id, title = await _resolve_chat_from_message(client, message, "group")
    if chat_id is None:
        await message.reply_text("⚠️ Forward a message from the group, or send its numeric ID.")
        return
    status = await _bot_admin_status(client, chat_id)
    if status not in _ADMIN_STATUSES:
        await message.reply_text(
            "⚠️ I'm not an admin in that group (or couldn't check). Add me as admin there, then send it again."
        )
        return
    session_manager.clear(message.from_user.id)
    added = await db.add_channel("group", chat_id, title or str(chat_id))
    if added:
        await message.reply_text(f"✅ {title} connected — filters are now active there.")
    else:
        await message.reply_text("⚠️ That group is already connected.")


# ---------------------------------------------------------------------------
# d) settings: Filter (List/Remove)
# ---------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^filtermgmt:"))
async def filter_mgmt_router(client, cq: CallbackQuery) -> None:
    if await deny_if_not_owner(cq):
        return
    parts = cq.data.split(":")
    action = parts[1]

    if action == "list":
        saved = await db.list_filters()
        if not saved:
            await cq.answer("No filters saved yet.", show_alert=True)
            return
        text = "🔍 <b>Saved filters</b>\n\n" + "\n".join(f"• {f['display']}" for f in saved)
        await cq.message.reply_text(text)
        await cq.answer()
        return
    if action == "rm" and len(parts) > 2:
        await db.remove_filter(parts[2])
        await cq.answer("Removed.")
    saved = await db.list_filters()
    if not saved:
        await cq.message.edit_text("🔍 <b>Filter</b>\n\n0 saved. Add new ones via /search in PM.",
                                    reply_markup=filter_settings_menu())
    else:
        await cq.message.edit_text("🗑️ Tap a filter to remove it.", reply_markup=filter_remove_menu(saved))
    if action != "rm":
        await cq.answer()

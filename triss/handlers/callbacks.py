"""
triss.handlers.callbacks
=========================
Every inline-button press in the settings UI, plus the small text/media
capture handlers that follow a button press when more input is needed
(e.g. "Set Welcome" -> owner sends the new text next).

CRITICAL: every single callback handler in this module re-validates
OWNER_ID server-side via `deny_if_not_owner`. Callback data itself is
never trusted for authorization — only for routing.
"""

from __future__ import annotations

import logging
from typing import Optional

from pyrogram import filters
from pyrogram.types import CallbackQuery, Message, LinkPreviewOptions
from pyrogram.errors import RPCError

from triss.bot import app
from triss.database import models as db
from triss.handlers.start import continue_after_force_sub
from triss.services import forcesub
from triss.services.backup import (
    create_backup, get_latest_backup_info, validate_backup, restore_backup,
    delete_backup, InvalidBackupError,
)
from triss.services.cleanup import session_manager, session_is
from triss.utils.auth import deny_if_not_owner
from triss.utils.formatting import (
    DEFAULT_WELCOME_TEXT, render_welcome, FORCE_SUB_TEXT, mask_secret,
    SHORTENER_VERIFY_TEXT, SHORTENER_BYPASS_TEXT, SHORTENER_MUTED_TEXT,
)
from triss.utils.keyboards import (
    settings_main_menu, welcome_menu, sticker_menu, forcesub_menu,
    forcesub_join_mode_menu,
    force_sub_user_keyboard, remove_forcesub_list, autodelete_menu,
    maintenance_menu, backup_menu, confirm_restore_menu, storechannel_menu,
    shortener_menu, shortener_tutorial_menu, shortener_message_menu, antibypass_menu,
    cancel_only, back_btn,
)
from triss.utils.time_parser import parse_duration_to_seconds, format_seconds
from triss.utils.validators import (
    is_valid_chat_id, is_valid_folder_link, is_valid_invite_link,
    normalize_shortener_domain, is_valid_url, is_valid_api_key,
)

logger = logging.getLogger("triss.handlers.callbacks")

_ADMIN_COMMANDS = ["genlink", "batch", "done", "cancelbatch", "broadcast", "settings", "start"]


async def _edit(cq: CallbackQuery, text: str, markup=None) -> None:
    try:
        await cq.message.edit_text(
            text, reply_markup=markup, link_preview_options=LinkPreviewOptions(is_disabled=True)
        )
    except RPCError:
        # e.g. MessageNotModified when the text/markup is unchanged — harmless
        pass


# ---------------------------------------------------------------------------
# Top-level settings navigation
# ---------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^settings:"))
async def settings_router(client, cq: CallbackQuery) -> None:
    if await deny_if_not_owner(cq):
        return
    action = cq.data.split(":", 1)[1]
    settings = await db.get_settings()

    if action == "main":
        await _edit(cq, "⚙️ ᴛʀɪss sᴇᴛᴛɪɴɢs", settings_main_menu())
    elif action == "welcome":
        await _edit(cq, "🏠 ᴡᴇʟᴄᴏᴍᴇ sᴇᴛᴛɪɴɢs", welcome_menu())
    elif action == "links":
        await _edit(
            cq,
            "🌐 ᴘʀɪᴠᴀᴛᴇ ʟɪɴᴋs\n\n"
            "Use /genlink for a single item, or /batch for a first-to-last range.",
            settings_main_menu(),
        )
    elif action == "forcesub":
        await _edit(cq, "📣 ғᴏʀᴄᴇ sᴜʙ sᴇᴛᴛɪɴɢs", forcesub_menu())
    elif action == "autodelete":
        ad = settings.get("auto_delete", {})
        status = "✅ Enabled" if ad.get("enabled") else "🚫 Disabled"
        current = format_seconds(int(ad.get("seconds", 0))) if ad.get("seconds") else "not set"
        notify = ad.get("notify", True)
        await _edit(cq, f"🧹 ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ\n\nStatus: {status}\nDuration: {current}\n"
                        f"Notify popup: {'✅ On' if notify else '🚫 Off (deletes silently)'}",
                    autodelete_menu(notify))
    elif action == "maintenance":
        status = "🧑‍🔧 Maintenance" if settings.get("maintenance") else "🤸 Active"
        await _edit(cq, f"⚙️ ʙᴏᴛ ᴍᴀɪɴᴛᴇɴᴀɴᴄᴇ\n\nCurrent status: {status}", maintenance_menu())
    elif action == "backup":
        await _edit(cq, "🗄️ ʙᴀᴄᴋᴜᴘ & ʀᴇsᴛᴏʀᴇ", backup_menu())
    elif action == "storechannel":
        configured = bool(settings.get("storage_channel_id"))
        text = "🏪 sᴛᴏʀᴇ ᴄʜᴀɴɴᴇʟ\n\n"
        text += "Status: ✅ Configured" if configured else "Status: ❌ Not configured"
        await _edit(cq, text, storechannel_menu(configured))
    elif action == "shortener":
        await _edit(cq, _shortener_status_text(settings.get("shortener", {})),
                    shortener_menu(bool(settings.get("shortener", {}).get("enabled"))))
    await cq.answer()


# ---------------------------------------------------------------------------
# Welcome submenu
# ---------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^welcome:"))
async def welcome_router(client, cq: CallbackQuery) -> None:
    if await deny_if_not_owner(cq):
        return
    action = cq.data.split(":", 1)[1]
    user_id = cq.from_user.id

    if action == "setphoto":
        session_manager.set(user_id, "welcome_set_photo")
        await cq.message.reply_text(
            "📸 Send the new welcome photo now. If you add a caption, it "
            "becomes the welcome text (photo + text are one message).",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "settext":
        session_manager.set(user_id, "welcome_set_text")
        await cq.message.reply_text(
            "💬 Send the new welcome text now. Supports {mention} {first} "
            "{last} {username} {id}.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "spoiler":
        settings = await db.get_settings()
        new_value = not settings.get("welcome", {}).get("spoiler", False)
        await db.update_settings({"welcome.spoiler": new_value})
        await cq.answer(f"Spoiler image {'enabled' if new_value else 'disabled'}.", show_alert=True)
        return
    elif action == "sticker":
        await _edit(cq, "🎀 sᴛɪᴄᴋᴇʀ sᴇᴛᴛɪɴɢs", sticker_menu())
    elif action.startswith("speed:"):
        speed = action.split(":", 1)[1]
        await db.update_settings({"welcome.animation_speed": speed})
        await cq.answer(f"Animation speed set to {speed}.", show_alert=True)
        return
    elif action == "preview":
        settings = await db.get_settings()
        welcome = settings.get("welcome", {})
        text = render_welcome(
            welcome.get("text") or DEFAULT_WELCOME_TEXT,
            user_id=cq.from_user.id,
            first_name=cq.from_user.first_name or "there",
            last_name=cq.from_user.last_name,
            username=cq.from_user.username,
        )
        photo_id = welcome.get("photo_file_id")
        if photo_id:
            await client.send_photo(cq.from_user.id, photo_id, caption=text,
                                     has_spoiler=bool(welcome.get("spoiler")))
        else:
            await client.send_message(
                cq.from_user.id, text, link_preview_options=LinkPreviewOptions(is_disabled=True)
            )
        if welcome.get("sticker_enabled") and welcome.get("sticker_file_id"):
            await client.send_sticker(cq.from_user.id, welcome["sticker_file_id"])
    await cq.answer()


@app.on_callback_query(filters.regex(r"^sticker:"))
async def sticker_router(client, cq: CallbackQuery) -> None:
    if await deny_if_not_owner(cq):
        return
    action = cq.data.split(":", 1)[1]
    user_id = cq.from_user.id
    settings = await db.get_settings()
    welcome = settings.get("welcome", {})

    if action == "set":
        session_manager.set(user_id, "welcome_set_sticker")
        await cq.message.reply_text("🎀 Send the sticker to use for the welcome message.",
                                     reply_markup=cancel_only("generic:cancel"))
    elif action == "remove":
        await db.update_settings({"welcome.sticker_file_id": None, "welcome.sticker_enabled": False})
        await cq.answer("Sticker removed.", show_alert=True)
        return
    elif action == "enable":
        if not welcome.get("sticker_file_id"):
            await cq.answer("Set a sticker first.", show_alert=True)
            return
        await db.update_settings({"welcome.sticker_enabled": True})
        await cq.answer("Sticker enabled.", show_alert=True)
        return
    elif action == "disable":
        await db.update_settings({"welcome.sticker_enabled": False})
        await cq.answer("Sticker disabled.", show_alert=True)
        return
    elif action == "preview":
        if welcome.get("sticker_file_id"):
            await client.send_sticker(user_id, welcome["sticker_file_id"])
        else:
            await cq.answer("No sticker configured.", show_alert=True)
            return
    await cq.answer()


# ---------------------------------------------------------------------------
# Force Sub submenu
# ---------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^forcesub:"))
async def forcesub_router(client, cq: CallbackQuery) -> None:
    action = cq.data.split(":", 1)[1]
    user_id = cq.from_user.id

    if action == "verify":
        # anyone (not just owner) can press Verify on the force-sub prompt
        unsatisfied = await forcesub.get_unsatisfied_requirements(client, user_id)
        if unsatisfied:
            await cq.answer("❌ You haven't joined all required chats yet.", show_alert=True)
            return
        await cq.answer("✅ Verified!", show_alert=True)
        session = session_manager.get(user_id)
        if session and session.kind == "forcesub_pending_token":
            token = session.data.get("token")
            session_manager.clear(user_id)
            if token:
                settings = await db.get_settings()
                # Route through the same continuation as a fresh access so
                # Shortener verification (if enabled) still applies here —
                # satisfying Force Sub must not skip Shortener.
                await continue_after_force_sub(client, cq.message, user_id, token, settings)
        try:
            await cq.message.delete()
        except RPCError:
            pass
        return

    # Everything else here is owner-only configuration.
    if await deny_if_not_owner(cq):
        return

    if action == "addchannel":
        await _edit(cq, "📣 Aᴅᴅ Cʜᴀɴɴᴇʟ — how should users join?", forcesub_join_mode_menu("channel"))
    elif action.startswith("addchannel:"):
        join_mode = action.split(":", 1)[1]
        session_manager.set(user_id, "forcesub_add_channel", {"join_mode": join_mode})
        prompt = ("Sᴇɴᴅ ᴀɴʏ ғᴏʀᴡᴀʀᴅᴇᴅ ᴍᴇssᴀɢᴇ ғʀᴏᴍ ᴛʜᴇ ᴄʜᴀɴɴᴇʟ, ᴏʀ sᴇɴᴅ ᴛʜᴇ Cʜᴀɴɴᴇʟ ID ᴅɪʀᴇᴄᴛʟʏ. 🔗\n\n"
                  f"Join mode: {'📝 Join Request' if join_mode == 'request' else '🔗 Normal Join'}")
        await cq.message.reply_text(prompt, reply_markup=cancel_only("generic:cancel"))
    elif action == "addgroup":
        await _edit(cq, "👥 Aᴅᴅ Gʀᴏᴜᴘ — how should users join?", forcesub_join_mode_menu("group"))
    elif action.startswith("addgroup:"):
        join_mode = action.split(":", 1)[1]
        session_manager.set(user_id, "forcesub_add_group", {"join_mode": join_mode})
        prompt = ("Sᴇɴᴅ ᴀɴʏ ғᴏʀᴡᴀʀᴅᴇᴅ ᴍᴇssᴀɢᴇ ғʀᴏᴍ ᴛʜᴇ Gʀᴏᴜᴘ, ᴏʀ sᴇɴᴅ ᴛʜᴇ Gʀᴏᴜᴘ ID ᴅɪʀᴇᴄᴛʟʏ. 👥\n\n"
                  f"Join mode: {'📝 Join Request' if join_mode == 'request' else '🔗 Normal Join'}")
        await cq.message.reply_text(prompt, reply_markup=cancel_only("generic:cancel"))
    elif action == "addfolder":
        session_manager.set(user_id, "forcesub_add_folder")
        await cq.message.reply_text(
            "Sᴇɴᴅ ᴛʜᴇ Tᴇʟᴇɢʀᴀᴍ Fᴏʟᴅᴇʀ Lɪɴᴋ ᴛᴏ ᴀᴅᴅ ᴛʜᴇ ғᴏʟᴅᴇʀ ᴛᴏ Fᴏʀᴄᴇ Sᴜʙ. 📂",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "list":
        entries = await db.list_force_subs()
        if not entries:
            await cq.message.reply_text("📋 No Force Sub entries configured.")
        else:
            lines = ["📋 Force Sub entries:"]
            for e in entries:
                lines.append(f"• {e['kind'].title()}: {e.get('title') or e.get('chat_id') or e.get('invite_link')}")
            await cq.message.reply_text("\n".join(lines))
    elif action == "remove":
        entries = await db.list_force_subs()
        if not entries:
            await cq.answer("Nothing to remove.", show_alert=True)
            return
        await _edit(cq, "❌ Select an entry to remove:", remove_forcesub_list(entries))
    elif action.startswith("rm:"):
        _, kind, chat_id_raw = action.split(":", 2)
        chat_id = int(chat_id_raw) if chat_id_raw not in ("None", "") else None
        removed = await db.remove_force_sub(kind, chat_id)
        await cq.answer("Removed." if removed else "Not found.", show_alert=True)
        entries = await db.list_force_subs()
        if entries:
            await _edit(cq, "❌ Select an entry to remove:", remove_forcesub_list(entries))
        else:
            await _edit(cq, "📣 ғᴏʀᴄᴇ sᴜʙ sᴇᴛᴛɪɴɢs", forcesub_menu())
        return
    elif action == "clear":
        count = await db.clear_force_subs()
        await cq.answer(f"Cleared {count} entrie(s).", show_alert=True)
        return
    await cq.answer()


# ---------------------------------------------------------------------------
# Auto Delete submenu
# ---------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^autodelete:"))
async def autodelete_router(client, cq: CallbackQuery) -> None:
    if await deny_if_not_owner(cq):
        return
    action = cq.data.split(":", 1)[1]
    user_id = cq.from_user.id

    if action.startswith("set:"):
        seconds = int(action.split(":", 1)[1])
        await db.update_settings({"auto_delete.seconds": seconds})
        await cq.answer(f"Auto-delete duration set to {format_seconds(seconds)}.", show_alert=True)
        return
    elif action == "custom":
        session_manager.set(user_id, "autodelete_custom")
        await cq.message.reply_text(
            "✏️ Send the custom duration, e.g. `10s`, `5m`, `2h`.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "enable":
        settings = await db.get_settings()
        if not settings.get("auto_delete", {}).get("seconds"):
            await cq.answer("Set a duration first.", show_alert=True)
            return
        await db.update_settings({"auto_delete.enabled": True})
        await cq.answer("Auto-delete enabled.", show_alert=True)
        return
    elif action == "disable":
        await db.update_settings({"auto_delete.enabled": False})
        await cq.answer("Auto-delete disabled.", show_alert=True)
        return
    elif action == "togglenotify":
        settings = await db.get_settings()
        new_value = not settings.get("auto_delete", {}).get("notify", True)
        await db.update_settings({"auto_delete.notify": new_value})
        settings = await db.get_settings()
        ad = settings.get("auto_delete", {})
        status = "✅ Enabled" if ad.get("enabled") else "🚫 Disabled"
        current = format_seconds(int(ad.get("seconds", 0))) if ad.get("seconds") else "not set"
        await _edit(cq, f"🧹 ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ\n\nStatus: {status}\nDuration: {current}\n"
                        f"Notify popup: {'✅ On' if new_value else '🚫 Off (deletes silently)'}",
                    autodelete_menu(new_value))
        await cq.answer(f"Auto-delete notification {'enabled' if new_value else 'disabled'}.", show_alert=True)
        return
    await cq.answer()


# ---------------------------------------------------------------------------
# Maintenance submenu
# ---------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^maintenance:"))
async def maintenance_router(client, cq: CallbackQuery) -> None:
    if await deny_if_not_owner(cq):
        return
    action = cq.data.split(":", 1)[1]
    await db.update_settings({"maintenance": action == "on"})
    status = "🧑‍🔧 Maintenance" if action == "on" else "🤸 Active"
    await _edit(cq, f"⚙️ ʙᴏᴛ ᴍᴀɪɴᴛᴇɴᴀɴᴄᴇ\n\nCurrent status: {status}", maintenance_menu())
    await cq.answer()


# ---------------------------------------------------------------------------
# Backup & Restore submenu
# ---------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^backup:"))
async def backup_router(client, cq: CallbackQuery) -> None:
    if await deny_if_not_owner(cq):
        return
    action = cq.data.split(":", 1)[1]

    if action == "create":
        payload = await create_backup()
        await cq.message.reply_text(
            f"💾 Backup created.\n\nForce Sub entries: {len(payload['force_subs'])}\n"
            f"Timestamp: {payload['created_at']:.0f}"
        )
    elif action == "restore":
        latest = await get_latest_backup_info()
        if latest is None:
            await cq.answer("No backup available.", show_alert=True)
            return
        await cq.message.reply_text(
            "⚠️ This will overwrite current settings and Force Sub entries "
            "with the latest backup. This cannot be undone. Continue?",
            reply_markup=confirm_restore_menu(),
        )
    elif action == "restore:confirm":
        latest = await get_latest_backup_info()
        if latest is None:
            await cq.answer("No backup available.", show_alert=True)
            return
        try:
            validate_backup(latest)
            await restore_backup(latest)
            await _edit(cq, "♻️ Backup restored successfully.", backup_menu())
        except InvalidBackupError as e:
            await _edit(cq, f"❌ Restore aborted — backup invalid: {e}", backup_menu())
    elif action == "restore:cancel":
        await _edit(cq, "🗄️ ʙᴀᴄᴋᴜᴘ & ʀᴇsᴛᴏʀᴇ", backup_menu())
    elif action == "info":
        latest = await get_latest_backup_info()
        if latest is None:
            await cq.answer("No backup available.", show_alert=True)
            return
        await cq.message.reply_text(
            f"📋 Latest backup\n\n"
            f"Force Sub entries: {len(latest.get('force_subs', []))}\n"
            f"Schema version: {latest.get('schema_version')}\n"
            f"Created at (unix): {latest.get('created_at'):.0f}"
        )
    elif action == "delete":
        deleted = await delete_backup()
        await cq.answer("Backup deleted." if deleted else "No backup to delete.", show_alert=True)
        return
    await cq.answer()


# ---------------------------------------------------------------------------
# Store Channel submenu
# ---------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^storechannel:"))
async def storechannel_router(client, cq: CallbackQuery) -> None:
    if await deny_if_not_owner(cq):
        return
    action = cq.data.split(":", 1)[1]
    user_id = cq.from_user.id

    if action == "set":
        session_manager.set(user_id, "storechannel_setup")
        await cq.message.reply_text(
            "🏪 Forward any message from the Store Channel, or send its numeric ID directly.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "unset":
        await db.update_settings({"storage_channel_id": None})
        settings = await db.get_settings()
        await _edit(cq, "🏪 sᴛᴏʀᴇ ᴄʜᴀɴɴᴇʟ\n\nStatus: ❌ Not configured", storechannel_menu(False))
    await cq.answer()


# ---------------------------------------------------------------------------
# Generic cancel (clears whatever session is active)
# ---------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^(generic|genlink|batch|broadcast):cancel$"))
async def generic_cancel(client, cq: CallbackQuery) -> None:
    if await deny_if_not_owner(cq):
        return
    session_manager.clear(cq.from_user.id)
    await cq.answer("Cancelled.", show_alert=True)
    try:
        await cq.message.delete()
    except RPCError:
        pass


# ---------------------------------------------------------------------------
# Session-driven text/media capture handlers
# ---------------------------------------------------------------------------

def _forwarded_chat(message: Message):
    return getattr(message, "forward_from_chat", None)


@app.on_message(filters.private & session_is("welcome_set_photo") & filters.photo)
async def capture_welcome_photo(client, message: Message) -> None:
    session_manager.clear(message.from_user.id)
    patch = {"welcome.photo_file_id": message.photo.file_id}
    if message.caption:
        patch["welcome.text"] = message.caption
    await db.update_settings(patch)
    await message.reply_text("✅ Welcome photo updated.")


@app.on_message(filters.private & session_is("welcome_set_text") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_welcome_text(client, message: Message) -> None:
    session_manager.clear(message.from_user.id)
    # `message.text` is the plain, entity-stripped string — any bold/italic/
    # etc. the owner applied while typing is lost before it ever reaches the
    # database. `.markdown` renders those entities back into markdown syntax
    # (**bold**, __italic__, ...) so it displays correctly when this text is
    # sent back out later (reply_text's default parse mode understands it).
    await db.update_settings({"welcome.text": message.text.markdown})
    await message.reply_text("✅ Welcome text updated.")


@app.on_message(filters.private & session_is("welcome_set_sticker") & filters.sticker)
async def capture_welcome_sticker(client, message: Message) -> None:
    session_manager.clear(message.from_user.id)
    await db.update_settings({"welcome.sticker_file_id": message.sticker.file_id, "welcome.sticker_enabled": True})
    await message.reply_text("✅ Sticker set and enabled.")


@app.on_message(filters.private & session_is("autodelete_custom") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_autodelete_custom(client, message: Message) -> None:
    seconds = parse_duration_to_seconds(message.text)
    if seconds is None:
        await message.reply_text("⚠️ Invalid duration. Use formats like `10s`, `5m`, `2h`.")
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"auto_delete.seconds": seconds})
    await message.reply_text(f"✅ Auto-delete duration set to {format_seconds(seconds)}.")


async def _resolve_chat_from_message(client, message: Message, kind: str):
    forwarded = _forwarded_chat(message)
    if forwarded is not None:
        return forwarded.id, (forwarded.title or forwarded.username or str(forwarded.id))
    if message.text and is_valid_chat_id(message.text):
        chat_id = int(message.text.strip())
        try:
            chat = await client.get_chat(chat_id)
            title = chat.title or chat.username or str(chat_id)
        except RPCError:
            title = str(chat_id)
        return chat_id, title
    return None, None


async def _export_invite_link(client, chat_id: int, join_mode: str) -> Optional[str]:
    """Normal Join reuses the chat's existing primary invite link
    (export_chat_invite_link) exactly as before. Join Request mode needs
    a NEW link created with creates_join_request=True — Telegram then
    shows users a "Request to Join" screen instead of joining them
    instantly; triss.services.forcesub's chat-join-request handler
    auto-approves those requests immediately, so the existing
    get_chat_member-based verification (get_unsatisfied_requirements)
    keeps working unchanged for both modes."""
    try:
        if join_mode == "request":
            link_obj = await client.create_chat_invite_link(chat_id, creates_join_request=True)
            return link_obj.invite_link
        return await client.export_chat_invite_link(chat_id)
    except RPCError:
        logger.info("Could not create/export invite link for %s (bot may not be admin yet).", chat_id)
        return None


@app.on_message(filters.private & session_is("forcesub_add_channel") & ~filters.command(_ADMIN_COMMANDS))
async def capture_forcesub_channel(client, message: Message) -> None:
    session = session_manager.get(message.from_user.id)
    join_mode = (session.data.get("join_mode") if session else None) or "normal"
    chat_id, title = await _resolve_chat_from_message(client, message, "channel")
    if chat_id is None:
        await message.reply_text("⚠️ Send a forwarded message from the channel, or its numeric ID.")
        return
    session_manager.clear(message.from_user.id)
    invite_link = await _export_invite_link(client, chat_id, join_mode)
    ok = await db.add_force_sub("channel", chat_id, title, invite_link, join_mode=join_mode)
    await message.reply_text("✅ Channel added to Force Sub." if ok else "⚠️ That channel is already configured.")


@app.on_message(filters.private & session_is("forcesub_add_group") & ~filters.command(_ADMIN_COMMANDS))
async def capture_forcesub_group(client, message: Message) -> None:
    session = session_manager.get(message.from_user.id)
    join_mode = (session.data.get("join_mode") if session else None) or "normal"
    chat_id, title = await _resolve_chat_from_message(client, message, "group")
    if chat_id is None:
        await message.reply_text("⚠️ Send a forwarded message from the group, or its numeric ID.")
        return
    session_manager.clear(message.from_user.id)
    invite_link = await _export_invite_link(client, chat_id, join_mode)
    ok = await db.add_force_sub("group", chat_id, title, invite_link, join_mode=join_mode)
    await message.reply_text("✅ Group added to Force Sub." if ok else "⚠️ That group is already configured.")


@app.on_message(filters.private & session_is("forcesub_add_folder") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_forcesub_folder(client, message: Message) -> None:
    link = message.text.strip()
    if not is_valid_folder_link(link):
        await message.reply_text("⚠️ That doesn't look like a valid Telegram Folder link "
                                  "(expected `https://t.me/addlist/...`).")
        return
    session_manager.clear(message.from_user.id)
    ok = await db.add_force_sub("folder", None, "Folder", link)
    await message.reply_text(
        "✅ Folder added as a Force Sub resource link.\n\n"
        "Note: Telegram does not provide an API to verify folder membership, "
        "so this entry is shown to users as a join resource but cannot itself "
        "block access." if ok else "⚠️ Could not add that folder."
    )


@app.on_message(filters.private & session_is("storechannel_setup") & ~filters.command(_ADMIN_COMMANDS))
async def capture_storechannel(client, message: Message) -> None:
    chat_id, title = await _resolve_chat_from_message(client, message, "store")
    if chat_id is None:
        await message.reply_text("⚠️ Forward a message from the Store Channel, or send its numeric ID.")
        return
    session_manager.clear(message.from_user.id)
    try:
        await client.get_chat(chat_id)
    except RPCError:
        await message.reply_text(
            "⚠️ Could not access that chat. Make sure the bot is an admin there, then try again."
        )
        return
    await db.update_settings({"storage_channel_id": chat_id})
    await message.reply_text("✅ Store Channel configured.")


# ---------------------------------------------------------------------------
# Shortener submenu
# ---------------------------------------------------------------------------

def _shortener_status_text(shortener_settings: dict) -> str:
    status = "🟢 ON" if shortener_settings.get("enabled") else "🔴 OFF"
    domain = shortener_settings.get("domain") or "Not set"
    api_display = mask_secret(shortener_settings.get("api_key"))
    minimum = shortener_settings.get("minimum_seconds", 0)
    maximum = shortener_settings.get("maximum_seconds", 0)
    tutorial = "Configured" if shortener_settings.get("tutorial_url") else "Not set"
    anti_bypass = shortener_settings.get("anti_bypass", {})
    strikes = anti_bypass.get("strike_limit", 3)
    mute_s = anti_bypass.get("mute_seconds", 600)
    return (
        "🌐 sʜᴏʀᴛᴇɴᴇʀ\n\n"
        f"Status: {status}\n"
        f"Domain: {domain}\n"
        f"API: `{api_display}`\n"
        f"Minimum: {minimum}s\n"
        f"Maximum: {maximum}s\n"
        f"Tutorial: {tutorial}\n"
        f"Anti-Bypass: {strikes} strikes / {mute_s}s mute\n\n"
        "ℹ️ This checks the *time window* between link creation and the user "
        "returning — it does not confirm the shortener page/ad was actually "
        "completed. Treat it as a delay gate, not a completion proof."
    )


_POPUP_KINDS = {
    "verifymsg": ("verify_message", SHORTENER_VERIFY_TEXT, "💬 Verify Popup"),
    "bypassmsg": ("bypass_message", SHORTENER_BYPASS_TEXT, "🚨 Bypass Popup"),
    "mutedmsg": ("muted_message", SHORTENER_MUTED_TEXT, "🔇 Muted Popup"),
}


async def _show_popup_menu(cq: CallbackQuery, kind: str, popup_settings: dict) -> None:
    field_name, _default, title = _POPUP_KINDS[kind]
    has_photo = bool(popup_settings.get("photo_file_id"))
    spoiler = bool(popup_settings.get("spoiler"))
    custom = "Custom text set" if popup_settings.get("text") else "Using default text"
    await _edit(
        cq,
        f"{title}\n\n{custom}\nPhoto: {'✅ Set' if has_photo else '❌ Not set'}\n"
        f"Spoiler: {'🟢 ON' if spoiler else '🔴 OFF'}",
        shortener_message_menu(kind, has_photo, spoiler),
    )


@app.on_callback_query(filters.regex(r"^shortener:"))
async def shortener_router(client, cq: CallbackQuery) -> None:
    action = cq.data.split(":", 1)[1]
    user_id = cq.from_user.id

    if action.startswith("retry:"):
        # Any user (not just owner) can retry their own verification.
        access_token = action.split(":", 1)[1]
        from triss.services import shortener as shortener_service

        settings = await db.get_settings()
        anti_bypass = settings.get("shortener", {}).get("anti_bypass", {})
        strike_limit = int(anti_bypass.get("strike_limit", 3))
        mute_seconds = int(anti_bypass.get("mute_seconds", 600))
        if shortener_service.is_rate_limited(user_id, threshold=strike_limit, window_seconds=mute_seconds):
            await cq.answer("Too many failed attempts — please wait a few minutes.", show_alert=True)
            return

        link_doc = await db.get_link(access_token)
        if link_doc is None or link_doc.get("revoked"):
            await cq.answer("This link is no longer valid.", show_alert=True)
            return

        # Re-check Force Sub in case it lapsed since the original attempt.
        if settings.get("force_sub_enabled", True):
            unsatisfied = await forcesub.get_unsatisfied_requirements(client, user_id)
            if unsatisfied:
                entries = await forcesub.get_display_entries()
                await cq.message.edit_text(FORCE_SUB_TEXT, reply_markup=force_sub_user_keyboard(entries))
                session_manager.set(user_id, "forcesub_pending_token", {"token": access_token})
                await cq.answer()
                return

        shortener_settings = settings.get("shortener", {})
        if not shortener_settings.get("enabled"):
            # Shortener was turned off between attempts — just deliver.
            await continue_after_force_sub(client, cq.message, user_id, access_token, settings)
            await cq.answer()
            return

        await _start_new_verification_message(client, cq.message, user_id, access_token, shortener_settings)
        await cq.answer()
        return

    # Everything else here is owner-only configuration.
    if await deny_if_not_owner(cq):
        return

    settings = await db.get_settings()
    shortener_settings = settings.get("shortener", {})

    # --- Customizable popups: verifymsg / bypassmsg / mutedmsg ---
    for popup_kind, (field_name, default_text, _title) in _POPUP_KINDS.items():
        if action == popup_kind:
            await _show_popup_menu(cq, popup_kind, shortener_settings.get(field_name, {}))
            await cq.answer()
            return
        if action.startswith(f"{popup_kind}:"):
            sub_action = action.split(":", 1)[1]
            popup_settings = shortener_settings.get(field_name, {})
            if sub_action == "settext":
                session_manager.set(user_id, f"shortener_set_{popup_kind}_text")
                await cq.message.reply_text(
                    "💬 Send the new popup text now.",
                    reply_markup=cancel_only("generic:cancel"),
                )
            elif sub_action == "setphoto":
                session_manager.set(user_id, f"shortener_set_{popup_kind}_photo")
                await cq.message.reply_text(
                    "📸 Send the photo now. If you add a caption, it becomes the popup text too.",
                    reply_markup=cancel_only("generic:cancel"),
                )
            elif sub_action == "removephoto":
                await db.update_settings({f"shortener.{field_name}.photo_file_id": None})
                await cq.answer("Photo removed.", show_alert=True)
                settings = await db.get_settings()
                await _show_popup_menu(cq, popup_kind, settings["shortener"].get(field_name, {}))
                return
            elif sub_action == "spoiler":
                new_value = not popup_settings.get("spoiler", False)
                await db.update_settings({f"shortener.{field_name}.spoiler": new_value})
                await cq.answer(f"Spoiler {'enabled' if new_value else 'disabled'}.", show_alert=True)
                settings = await db.get_settings()
                await _show_popup_menu(cq, popup_kind, settings["shortener"].get(field_name, {}))
                return
            elif sub_action == "reset":
                await db.update_settings({
                    f"shortener.{field_name}.text": None,
                    f"shortener.{field_name}.photo_file_id": None,
                    f"shortener.{field_name}.spoiler": False,
                })
                await cq.answer("Reset to default.", show_alert=True)
                await _show_popup_menu(cq, popup_kind, {})
                return
            elif sub_action == "preview":
                text = popup_settings.get("text") or default_text
                photo_id = popup_settings.get("photo_file_id")
                if photo_id:
                    await client.send_photo(user_id, photo_id, caption=text,
                                             has_spoiler=bool(popup_settings.get("spoiler")))
                else:
                    await client.send_message(user_id, text,
                                               link_preview_options=LinkPreviewOptions(is_disabled=True))
            await cq.answer()
            return

    # --- Anti-Bypass submenu ---
    if action == "antibypass":
        ab = shortener_settings.get("anti_bypass", {})
        await _edit(cq, "🎯 ᴀɴᴛɪ-ʙʏᴘᴀss sᴇᴛᴛɪɴɢs\n\n"
                        "Strikes 1..(limit-1) show the Bypass Popup with a live warning "
                        "count. The strike that reaches the limit shows the Muted Popup "
                        "instead and blocks further attempts for the mute duration "
                        "(never shown to the user).",
                    antibypass_menu(ab.get("strike_limit", 3), ab.get("mute_seconds", 600)))
        await cq.answer()
        return
    elif action == "antibypass:setstrikes":
        session_manager.set(user_id, "shortener_set_strike_limit")
        await cq.message.reply_text(
            "🎯 Send the strike limit (whole number, e.g. `3`). The Nth bypass attempt "
            "triggers the mute.",
            reply_markup=cancel_only("generic:cancel"),
        )
        await cq.answer()
        return
    elif action == "antibypass:setmute":
        session_manager.set(user_id, "shortener_set_mute_seconds")
        await cq.message.reply_text(
            "🔇 Send the mute duration in whole seconds, e.g. `600`. This is never shown "
            "to the user.",
            reply_markup=cancel_only("generic:cancel"),
        )
        await cq.answer()
        return

    if action == "setdomain":
        session_manager.set(user_id, "shortener_set_domain")
        await cq.message.reply_text(
            "🌍 Send the shortener domain, e.g. `example.com` or `https://example.com`.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "setapi":
        session_manager.set(user_id, "shortener_set_api")
        await cq.message.reply_text(
            "🔒 Send the shortener API key/token. It will never be shown in full again.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "setmin":
        session_manager.set(user_id, "shortener_set_min")
        await cq.message.reply_text(
            "🕒 Send the minimum verification time in whole seconds, e.g. `150`.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "setmax":
        session_manager.set(user_id, "shortener_set_max")
        await cq.message.reply_text(
            "⏰ Send the maximum verification time in whole seconds, e.g. `500`.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "tutorial":
        configured = bool(shortener_settings.get("tutorial_url"))
        await _edit(cq, "▶️ ᴛᴜᴛᴏʀɪᴀʟ ᴠɪᴅᴇᴏ", shortener_tutorial_menu(configured))
    elif action == "tutorial:set":
        session_manager.set(user_id, "shortener_set_tutorial")
        await cq.message.reply_text(
            "▶️ Send the tutorial video URL, e.g. `https://example.com/tutorial`.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "tutorial:remove":
        await db.update_settings({"shortener.tutorial_url": None})
        await cq.answer("Tutorial video removed.", show_alert=True)
        await _edit(cq, "▶️ ᴛᴜᴛᴏʀɪᴀʟ ᴠɪᴅᴇᴏ", shortener_tutorial_menu(False))
        return
    elif action == "toggle":
        if not shortener_settings.get("enabled"):
            # Turning ON: require domain + API key to already be configured,
            # otherwise every access would silently fail at verification time.
            if not shortener_settings.get("domain") or not shortener_settings.get("api_key"):
                await cq.answer("Set a Domain and API key before enabling the Shortener.", show_alert=True)
                return
            if shortener_settings.get("maximum_seconds", 0) < shortener_settings.get("minimum_seconds", 0):
                await cq.answer("Maximum time must be >= Minimum time before enabling.", show_alert=True)
                return
            # NOTE: Shortener verification here is time-window gating only
            # (elapsed time between session creation and the user opening the
            # deep link, bounded by Minimum/Maximum Time below), evaluated
            # entirely inside Telegram — no web page or PUBLIC_BASE_URL is
            # involved or required. It is not genuine provider-side
            # completion verification. See the module docstring in
            # triss/services/shortener.py for exactly what this does and
            # does not protect against.
        new_value = not shortener_settings.get("enabled", False)
        await db.update_settings({"shortener.enabled": new_value})
        settings = await db.get_settings()
        await _edit(cq, _shortener_status_text(settings["shortener"]), shortener_menu(new_value))
        await cq.answer(f"Shortener {'enabled' if new_value else 'disabled'}.", show_alert=True)
        return
    await cq.answer()


async def _start_new_verification_message(client, message: Message, user_id: int, access_token: str,
                                           shortener_settings: dict) -> None:
    """Used by the 'Try Again' retry path: creates a brand-new verification
    session (never reusing the old one) and shows the verification prompt
    again, exactly like a first-time access."""
    from triss.services import shortener as shortener_service
    from triss.utils.formatting import SHORTENER_VERIFY_TEXT, SHORTENER_UNAVAILABLE_TEXT
    from triss.utils.keyboards import shortener_verification_keyboard
    from triss.handlers.start import _send_customizable_popup

    username = getattr(client, "username", None) or (await client.get_me()).username
    _session, short_url = await shortener_service.start_new_verification(
        username, user_id, access_token, shortener_settings
    )
    if short_url is None:
        await message.reply_text(SHORTENER_UNAVAILABLE_TEXT)
        return
    tutorial_url = shortener_settings.get("tutorial_url")
    await _send_customizable_popup(
        client, message, shortener_settings.get("verify_message", {}), SHORTENER_VERIFY_TEXT,
        reply_markup=shortener_verification_keyboard(short_url, tutorial_url),
    )


@app.on_message(filters.private & session_is("shortener_set_domain") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_shortener_domain(client, message: Message) -> None:
    domain = normalize_shortener_domain(message.text)
    if domain is None:
        await message.reply_text("⚠️ That doesn't look like a valid domain. Try `example.com`.")
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"shortener.domain": domain})
    await message.reply_text(f"✅ Shortener domain set to `{domain}`.")


@app.on_message(filters.private & session_is("shortener_set_api") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_shortener_api(client, message: Message) -> None:
    api_key = message.text.strip()
    if not is_valid_api_key(api_key):
        await message.reply_text("⚠️ That doesn't look like a valid API key (no spaces, 3-256 characters).")
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"shortener.api_key": api_key})
    # Best-effort: delete the owner's message containing the raw key so it
    # doesn't linger in chat history any longer than necessary.
    try:
        await message.delete()
    except RPCError:
        pass
    await message.reply_text(f"✅ Shortener API key updated: `{mask_secret(api_key)}`")


@app.on_message(filters.private & session_is("shortener_set_min") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_shortener_min(client, message: Message) -> None:
    text = message.text.strip()
    if not text.isdigit():
        await message.reply_text("⚠️ Send a whole number of seconds, e.g. `150`.")
        return
    seconds = int(text)
    if seconds < 0 or seconds > 24 * 3600:
        await message.reply_text("⚠️ Please choose a value between 0 and 86400 seconds.")
        return
    settings = await db.get_settings()
    maximum = settings.get("shortener", {}).get("maximum_seconds", 0)
    if seconds > maximum:
        await message.reply_text(
            f"⚠️ Minimum ({seconds}s) cannot be greater than the current Maximum ({maximum}s). "
            f"Set Maximum first, or choose a smaller Minimum."
        )
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"shortener.minimum_seconds": seconds})
    await message.reply_text(f"✅ Minimum verification time set to {seconds}s.")


@app.on_message(filters.private & session_is("shortener_set_max") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_shortener_max(client, message: Message) -> None:
    text = message.text.strip()
    if not text.isdigit():
        await message.reply_text("⚠️ Send a whole number of seconds, e.g. `500`.")
        return
    seconds = int(text)
    if seconds <= 0 or seconds > 24 * 3600:
        await message.reply_text("⚠️ Please choose a value between 1 and 86400 seconds.")
        return
    settings = await db.get_settings()
    minimum = settings.get("shortener", {}).get("minimum_seconds", 0)
    if seconds < minimum:
        await message.reply_text(
            f"⚠️ Maximum ({seconds}s) cannot be lower than the current Minimum ({minimum}s)."
        )
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"shortener.maximum_seconds": seconds})
    await message.reply_text(f"✅ Maximum verification time set to {seconds}s.")


@app.on_message(filters.private & session_is("shortener_set_tutorial") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_shortener_tutorial(client, message: Message) -> None:
    url = message.text.strip()
    if not is_valid_url(url):
        await message.reply_text("⚠️ Send a valid URL starting with http:// or https://.")
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"shortener.tutorial_url": url})
    await message.reply_text("✅ Tutorial video URL set.")


@app.on_message(filters.private & session_is("shortener_set_strike_limit") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_strike_limit(client, message: Message) -> None:
    text = message.text.strip()
    if not text.isdigit() or int(text) < 1:
        await message.reply_text("⚠️ Send a whole number of 1 or more, e.g. `3`.")
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"shortener.anti_bypass.strike_limit": int(text)})
    await message.reply_text(f"✅ Strike limit set to {text}.")


@app.on_message(filters.private & session_is("shortener_set_mute_seconds") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_mute_seconds(client, message: Message) -> None:
    text = message.text.strip()
    if not text.isdigit() or int(text) < 1:
        await message.reply_text("⚠️ Send a whole number of seconds, e.g. `600`.")
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"shortener.anti_bypass.mute_seconds": int(text)})
    await message.reply_text(f"✅ Mute duration set to {text}s (never shown to the user).")


# ---------------------------------------------------------------------------
# Customizable shortener popups (verify / bypass / muted) — text & photo
# capture handlers, generated once per popup kind to avoid repeating the
# same three handler pairs by hand.
# ---------------------------------------------------------------------------

def _register_popup_capture_handlers() -> None:
    for _popup_kind, (_field_name, _default_text, _title) in _POPUP_KINDS.items():
        text_session_kind = f"shortener_set_{_popup_kind}_text"
        photo_session_kind = f"shortener_set_{_popup_kind}_photo"

        async def _capture_popup_text(client, message: Message, field_name=_field_name) -> None:
            session_manager.clear(message.from_user.id)
            await db.update_settings({f"shortener.{field_name}.text": message.text.markdown})
            await message.reply_text("✅ Popup text updated.")

        async def _capture_popup_photo(client, message: Message, field_name=_field_name) -> None:
            session_manager.clear(message.from_user.id)
            patch = {f"shortener.{field_name}.photo_file_id": message.photo.file_id}
            if message.caption:
                patch[f"shortener.{field_name}.text"] = message.caption.markdown
            await db.update_settings(patch)
            await message.reply_text("✅ Popup photo updated.")

        app.on_message(
            filters.private & session_is(text_session_kind) & filters.text & ~filters.command(_ADMIN_COMMANDS)
        )(_capture_popup_text)
        app.on_message(
            filters.private & session_is(photo_session_kind) & filters.photo
        )(_capture_popup_photo)


_register_popup_capture_handlers()

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
from pyrogram.enums import ChatMemberStatus
from pyrogram.types import CallbackQuery, Message, LinkPreviewOptions
from pyrogram.errors import RPCError

from triss.bot import app
from triss.config import config
from triss.database import models as db
from triss.handlers.start import continue_after_force_sub
from triss.handlers.linkdl import deliver_linkdl, LINKDL_START_PREFIX
from triss.services import forcesub
from triss.services.backup import (
    create_backup, get_latest_backup_info, validate_backup, restore_backup,
    delete_backup, InvalidBackupError,
)
from triss.services.cleanup import session_manager, session_is
from triss.utils.auth import deny_if_not_owner, deny_if_not_super_owner, is_super_owner
from triss.utils.formatting import (
    DEFAULT_WELCOME_TEXT, render_welcome, FORCE_SUB_TEXT, mask_secret,
    SHORTENER_VERIFY_TEXT, SHORTENER_BYPASS_TEXT, SHORTENER_MUTED_TEXT,
)
from triss.utils.keyboards import (
    settings_main_menu, welcome_menu, sticker_menu, forcesub_menu,
    forcesub_join_mode_menu, forcesub_message_menu, forcesub_editbtn_list,
    force_sub_user_keyboard, remove_forcesub_list, autodelete_menu,
    maintenance_menu, backup_menu, confirm_restore_menu, channel_menu, channel_list_menu,
    premium_menu, group_settings_menu, rt_save_menu, rt_save_duration_menu,
    shortener_menu, shortener_tutorial_menu, shortener_message_menu, antibypass_menu,
    system_access_menu, system_access_duration_menu, format_duration, linkdl_menu,
    cancel_only, back_btn,
)
from triss.utils.time_parser import parse_duration_to_seconds, format_seconds
from triss.utils.validators import (
    is_valid_chat_id, is_valid_folder_link, is_valid_invite_link,
    normalize_shortener_domain, is_valid_url, is_valid_api_key,
)

logger = logging.getLogger("triss.handlers.callbacks")

_ADMIN_COMMANDS = ["genlink", "batch", "done", "cancelbatch", "autobatch", "broadcast", "settings", "start", "addadmin", "removeadmin", "listadmin", "mute", "unmute", "listmute", "ban", "unban", "listban", "linkdl", "setlinkcaption", "buy_premium", "setvip", "listvip", "deletevip", "search", "rt_save"]


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
        total_users = await db.count_users()
        await _edit(cq, f"⚙️ ᴛʀɪss sᴇᴛᴛɪɴɢs\n\n👥 Total bot users: {total_users:,}", settings_main_menu(settings))
    elif action == "welcome":
        await _edit(cq, "🏠 ᴡᴇʟᴄᴏᴍᴇ sᴇᴛᴛɪɴɢs", welcome_menu(settings.get("welcome", {}).get("media_type", "photo")))
    elif action == "links":
        from triss.handlers.help import _EVERYONE_SECTION, _OWNER_ADMIN_SECTION, _SUPER_OWNER_SECTION
        user_id = cq.from_user.id
        text = "💬 ᴄᴏᴍᴍᴇɴᴛs\n\nEvery command, and how to use it:\n" + _EVERYONE_SECTION + _OWNER_ADMIN_SECTION
        if is_super_owner(user_id):
            text += _SUPER_OWNER_SECTION
        text += (
            "\nℹ️ Custom text (welcome, popups, /setlinkcaption) supports HTML: "
            "<code>&lt;b&gt;bold&lt;/b&gt;</code>, <code>&lt;i&gt;italic&lt;/i&gt;</code>, "
            "<code>&lt;a href=\"url\"&gt;link&lt;/a&gt;</code> — or just use Telegram's own "
            "bold/italic formatting while typing, both are preserved."
        )
        await _edit(cq, text, settings_main_menu(settings))
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
    elif action in ("storechannel", "logchannel"):
        kind = "storage" if action == "storechannel" else "log"
        await _edit(cq, await _channel_status_text(kind), channel_menu(kind))
    elif action == "premium":
        await _edit(cq, "💎 <b>Premium</b>\n\nManual UPI-payment plans, owner-approved via screenshot.",
                    premium_menu(settings.get("premium", {})))
    elif action == "groupsettings":
        await _edit(cq, "👥 <b>Group Settings</b>\n\n🔗 Connect — which groups 🔍 Filter auto-replies work in.",
                    group_settings_menu())
    elif action == "rtsave":
        await _edit(cq,
                    "🔓 <b>Restrict Save</b>\n\n/rt_save lets a user unlock a message from a "
                    "Restrict-Saving-protected chat (the bot must be admin there). One shortener "
                    "verification grants free use for the Verify Time window below.",
                    rt_save_menu(settings.get("rt_save", {})))
    elif action == "shortener":
        await _edit(cq, _shortener_status_text(settings.get("shortener", {})),
                    shortener_menu(bool(settings.get("shortener", {}).get("enabled"))))
    elif action == "systemaccess":
        sa = settings.get("shortener", {}).get("system_access", {})
        await _edit(cq, _system_access_status_text(sa), system_access_menu(sa))
    elif action == "linkdl":
        ld = settings.get("linkdl", {})
        has_url = config.public_base_url is not None
        custom = "Custom caption set" if ld.get("text") or ld.get("caption") else "Using default caption"
        status_note = "" if has_url else "\n\n⚠️ PUBLIC_BASE_URL isn't configured — run /linkdl for details."
        await _edit(
            cq, f"📥 Dɪʀᴇᴄᴛ Dᴏᴡɴʟᴏᴀᴅ\n\n{custom}{status_note}",
            linkdl_menu(ld, has_url),
        )
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
    elif action == "setvideo":
        session_manager.set(user_id, "welcome_set_video")
        await cq.message.reply_text(
            "🎥 Send the new welcome video now. If you add a caption, it "
            "becomes the welcome text.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "setgif":
        session_manager.set(user_id, "welcome_set_gif")
        await cq.message.reply_text(
            "🎞️ Send the new welcome GIF now. If you add a caption, it "
            "becomes the welcome text.",
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
        video_id = welcome.get("video_file_id")
        gif_id = welcome.get("animation_file_id")
        media_type = welcome.get("media_type", "photo")
        spoiler = bool(welcome.get("spoiler"))
        if media_type == "video" and video_id:
            await client.send_video(cq.from_user.id, video_id, caption=text, has_spoiler=spoiler)
        elif media_type == "animation" and gif_id:
            await client.send_animation(cq.from_user.id, gif_id, caption=text, has_spoiler=spoiler)
        elif media_type == "photo" and photo_id:
            await client.send_photo(cq.from_user.id, photo_id, caption=text, has_spoiler=spoiler)
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
        token = await db.pop_pending_access(user_id)
        if token:
            if token.startswith(LINKDL_START_PREFIX):
                await deliver_linkdl(client, user_id, token[len(LINKDL_START_PREFIX):])
            else:
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
                label = e.get("title") or e.get("chat_id") or e.get("invite_link")
                mode = e.get("join_mode")
                mode_tag = " (📝 Join Request)" if mode == "request" else (" (🔗 Normal Join)" if mode else "")
                count_tag = ""
                if e.get("chat_id") is not None:
                    try:
                        count = await client.get_chat_members_count(e["chat_id"])
                        count_tag = f" — 👥 {count:,} members"
                    except RPCError:
                        count_tag = " — 👥 (count unavailable, is the bot still admin here?)"
                lines.append(f"• {e['kind'].title()}: {label}{mode_tag}{count_tag}")
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
    elif action == "message":
        settings = await db.get_settings()
        fs_msg = settings.get("force_sub_message", {})
        has_photo = bool(fs_msg.get("photo_file_id"))
        spoiler = bool(fs_msg.get("spoiler"))
        custom = "Custom text set" if fs_msg.get("text") else "Using default text"
        await _edit(
            cq, f"💬 Fᴏʀᴄᴇ Sᴜʙ Mᴇssᴀɢᴇ\n\n{custom}\nPhoto: {'✅ Set' if has_photo else '❌ Not set'}\n"
                f"Spoiler: {'ON' if spoiler else 'OFF'}",
            forcesub_message_menu(has_photo, spoiler),
        )
    elif action.startswith("message:"):
        sub_action = action.split(":", 1)[1]
        settings = await db.get_settings()
        fs_msg = settings.get("force_sub_message", {})
        if sub_action == "settext":
            session_manager.set(user_id, "forcesub_set_message_text")
            await cq.message.reply_text("💬 Send the new Force Sub message text now.",
                                         reply_markup=cancel_only("generic:cancel"))
        elif sub_action == "setphoto":
            session_manager.set(user_id, "forcesub_set_message_photo")
            await cq.message.reply_text(
                "📸 Send the photo now. If you add a caption, it becomes the message text too.",
                reply_markup=cancel_only("generic:cancel"),
            )
        elif sub_action == "removephoto":
            await db.update_settings({"force_sub_message.photo_file_id": None})
            await cq.answer("Photo removed.", show_alert=True)
            settings = await db.get_settings()
            fs_msg = settings.get("force_sub_message", {})
            await _edit(cq, "💬 Fᴏʀᴄᴇ Sᴜʙ Mᴇssᴀɢᴇ", forcesub_message_menu(
                bool(fs_msg.get("photo_file_id")), bool(fs_msg.get("spoiler"))))
            return
        elif sub_action == "spoiler":
            new_value = not fs_msg.get("spoiler", False)
            await db.update_settings({"force_sub_message.spoiler": new_value})
            await cq.answer(f"Spoiler {'enabled' if new_value else 'disabled'}.", show_alert=True)
            settings = await db.get_settings()
            fs_msg = settings.get("force_sub_message", {})
            await _edit(cq, "💬 Fᴏʀᴄᴇ Sᴜʙ Mᴇssᴀɢᴇ", forcesub_message_menu(
                bool(fs_msg.get("photo_file_id")), new_value))
            return
        elif sub_action == "reset":
            await db.update_settings({
                "force_sub_message.text": None,
                "force_sub_message.photo_file_id": None,
                "force_sub_message.spoiler": False,
            })
            await cq.answer("Reset to default.", show_alert=True)
            await _edit(cq, "💬 Fᴏʀᴄᴇ Sᴜʙ Mᴇssᴀɢᴇ", forcesub_message_menu(False, False))
            return
        elif sub_action == "preview":
            entries = await db.list_force_subs()
            text = fs_msg.get("text") or FORCE_SUB_TEXT
            photo_id = fs_msg.get("photo_file_id")
            if photo_id:
                await client.send_photo(user_id, photo_id, caption=text,
                                         has_spoiler=bool(fs_msg.get("spoiler")),
                                         reply_markup=force_sub_user_keyboard(entries))
            else:
                await client.send_message(user_id, text, reply_markup=force_sub_user_keyboard(entries))
        await cq.answer()
        return
    elif action == "editbtn":
        entries = await db.list_force_subs()
        if not entries:
            await cq.answer("No Force Sub entries yet.", show_alert=True)
            return
        await _edit(cq, "✏️ Tᴀᴘ ᴀɴ ᴇɴᴛʀʏ ᴛᴏ sᴇᴛ ɪᴛs ʙᴜᴛᴛᴏɴ ɴᴀᴍᴇ:", forcesub_editbtn_list(entries))
    elif action.startswith("editbtn:"):
        _, kind, chat_id_raw = action.split(":", 2)
        chat_id = int(chat_id_raw) if chat_id_raw not in ("None", "") else None
        session_manager.set(user_id, "forcesub_set_button_text", {"kind": kind, "chat_id": chat_id})
        await cq.message.reply_text(
            "✏️ Send the new button label for this entry (e.g. <code>🎬 Movie Updates</code>), "
            "or send <code>reset</code> to go back to the default label.",
            reply_markup=cancel_only("generic:cancel"),
        )
        await cq.answer()
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
# Direct download links submenu (item 6 - /linkdl)
# ---------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^linkdl:"))
async def linkdl_router(client, cq: CallbackQuery) -> None:
    if await deny_if_not_owner(cq):
        return
    action = cq.data.split(":", 1)[1]
    user_id = cq.from_user.id

    if action == "noop":
        await cq.answer(
            "Set PUBLIC_BASE_URL to this bot's own public HTTPS URL and restart — "
            "run /linkdl in chat for the full explanation.",
            show_alert=True,
        )
        return

    if action == "toggle":
        if await deny_if_not_super_owner(cq):
            return
        if not config.public_base_url:
            await cq.answer(
                "PUBLIC_BASE_URL isn't configured — run /linkdl in chat for details.",
                show_alert=True,
            )
            return
        settings = await db.get_settings()
        new_value = not settings.get("linkdl", {}).get("enabled", False)
        await db.update_settings({"linkdl.enabled": new_value})
        await cq.answer(f"Public Use {'enabled' if new_value else 'disabled'}.", show_alert=True)
        settings = await db.get_settings()
        await _edit(cq, "📥 Dɪʀᴇᴄᴛ Dᴏᴡɴʟᴏᴀᴅ",
                    linkdl_menu(settings.get("linkdl", {}), config.public_base_url is not None))
        return
    elif action == "settext":
        session_manager.set(user_id, "linkdl_set_caption")
        await cq.message.reply_text(
            "💬 Send the new caption now — it must include a <code>{link}</code> placeholder.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "setphoto":
        session_manager.set(user_id, "linkdl_set_photo")
        await cq.message.reply_text(
            "🖼️ Send the photo to use for the /linkdl reply now.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "setvideo":
        session_manager.set(user_id, "linkdl_set_video")
        await cq.message.reply_text(
            "🎥 Send the video to use for the /linkdl reply now.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "setgif":
        session_manager.set(user_id, "linkdl_set_gif")
        await cq.message.reply_text(
            "🎞️ Send the GIF to use for the /linkdl reply now.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "removemedia":
        await db.update_settings({
            "linkdl.media_type": "none", "linkdl.photo_file_id": None,
            "linkdl.video_file_id": None, "linkdl.animation_file_id": None,
        })
        await cq.answer("Media removed — back to text-only reply.", show_alert=True)
        settings = await db.get_settings()
        await _edit(cq, "📥 Dɪʀᴇᴄᴛ Dᴏᴡɴʟᴏᴀᴅ",
                    linkdl_menu(settings.get("linkdl", {}), config.public_base_url is not None))
        return
    elif action == "spoiler":
        settings = await db.get_settings()
        new_value = not settings.get("linkdl", {}).get("spoiler", False)
        await db.update_settings({"linkdl.spoiler": new_value})
        await cq.answer(f"Spoiler {'enabled' if new_value else 'disabled'}.", show_alert=True)
        settings = await db.get_settings()
        await _edit(cq, "📥 Dɪʀᴇᴄᴛ Dᴏᴡɴʟᴏᴀᴅ",
                    linkdl_menu(settings.get("linkdl", {}), config.public_base_url is not None))
        return
    elif action == "reset":
        await db.update_settings({"linkdl.caption": None})
        await cq.answer("Reset to default.", show_alert=True)
        settings = await db.get_settings()
        await _edit(cq, "📥 Dɪʀᴇᴄᴛ Dᴏᴡɴʟᴏᴀᴅ",
                    linkdl_menu(settings.get("linkdl", {}), config.public_base_url is not None))
        return
    await cq.answer()


@app.on_message(filters.private & session_is("linkdl_set_photo") & filters.photo)
async def capture_linkdl_photo(client, message: Message) -> None:
    if await deny_if_not_super_owner(message):
        session_manager.clear(message.from_user.id)
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"linkdl.photo_file_id": message.photo.file_id, "linkdl.media_type": "photo"})
    await message.reply_text("✅ /linkdl reply photo updated.")


@app.on_message(filters.private & session_is("linkdl_set_video") & filters.video)
async def capture_linkdl_video(client, message: Message) -> None:
    if await deny_if_not_super_owner(message):
        session_manager.clear(message.from_user.id)
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"linkdl.video_file_id": message.video.file_id, "linkdl.media_type": "video"})
    await message.reply_text("✅ /linkdl reply video updated.")


@app.on_message(filters.private & session_is("linkdl_set_gif") & filters.animation)
async def capture_linkdl_gif(client, message: Message) -> None:
    if await deny_if_not_super_owner(message):
        session_manager.clear(message.from_user.id)
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"linkdl.animation_file_id": message.animation.file_id, "linkdl.media_type": "animation"})
    await message.reply_text("✅ /linkdl reply GIF updated.")


@app.on_message(filters.private & session_is("linkdl_set_caption") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_linkdl_caption(client, message: Message) -> None:
    if await deny_if_not_super_owner(message):
        session_manager.clear(message.from_user.id)
        return
    session_manager.clear(message.from_user.id)
    if "{link}" not in message.text:
        await message.reply_text("⚠️ Your caption must include a <code>{link}</code> placeholder.")
        return
    await db.update_settings({"linkdl.caption": message.text.html})
    await message.reply_text("✅ Direct-download caption updated.")


# ---------------------------------------------------------------------------
# Maintenance submenu
# ---------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^maintenance:"))
async def maintenance_router(client, cq: CallbackQuery) -> None:
    if await deny_if_not_owner(cq):
        return
    action = cq.data.split(":", 1)[1]
    new_value = action == "on"
    await db.update_settings({"maintenance": new_value})
    from triss.utils.maintenance import set_maintenance_cache
    set_maintenance_cache(new_value)  # live immediately, no restart needed - see triss.handlers.maintenance_gate
    status = "🧑‍🔧 Maintenance" if new_value else "🤸 Active"
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

@app.on_callback_query(filters.regex(r"^rtsave:"))
async def rt_save_settings_router(client, cq: CallbackQuery) -> None:
    """Mirrors system_access_router exactly (see its docstring/comments) -
    rt_save has its own fully separate Domain/API/Min-Max/Popups/
    Anti-Bypass, namespace "rtsave", settings path "rt_save.*". Two
    extra toggles sysaccess doesn't have: Shortener (verification
    required at all) and Public Use (the /rt_save command itself)."""
    if await deny_if_not_owner(cq):
        return
    parts = cq.data.split(":", 1)
    action = parts[1] if len(parts) > 1 else ""
    user_id = cq.from_user.id

    settings = await db.get_settings()
    rt_save = settings.get("rt_save", {})

    for popup_kind, (field_name, default_text, _title) in _POPUP_KINDS.items():
        if popup_kind == "mutedmsg":
            continue  # rt_save has no mute step - no Muted Popup to configure
        if action == popup_kind:
            await _show_popup_menu(cq, popup_kind, rt_save.get(field_name, {}), namespace="rtsave")
            await cq.answer()
            return
        if action.startswith(f"{popup_kind}:"):
            sub_action = action.split(":", 1)[1]
            popup_settings = rt_save.get(field_name, {})
            path = f"rt_save.{field_name}"
            if sub_action == "settext":
                session_manager.set(user_id, f"rtsave_set_{popup_kind}_text")
                await cq.message.reply_text(
                    "💬 Send the new popup text now.",
                    reply_markup=cancel_only("generic:cancel"),
                )
            elif sub_action == "setphoto":
                session_manager.set(user_id, f"rtsave_set_{popup_kind}_photo")
                await cq.message.reply_text(
                    "📸 Send the photo now. If you add a caption, it becomes the popup text too.",
                    reply_markup=cancel_only("generic:cancel"),
                )
            elif sub_action == "removephoto":
                await db.update_settings({f"{path}.photo_file_id": None})
                await cq.answer("Photo removed.", show_alert=True)
                settings = await db.get_settings()
                rt_save = settings["rt_save"]
                await _show_popup_menu(cq, popup_kind, rt_save.get(field_name, {}), namespace="rtsave")
                return
            elif sub_action == "spoiler":
                new_value = not popup_settings.get("spoiler", False)
                await db.update_settings({f"{path}.spoiler": new_value})
                await cq.answer(f"Spoiler {'enabled' if new_value else 'disabled'}.", show_alert=True)
                settings = await db.get_settings()
                rt_save = settings["rt_save"]
                await _show_popup_menu(cq, popup_kind, rt_save.get(field_name, {}), namespace="rtsave")
                return
            elif sub_action == "reset":
                await db.update_settings({
                    f"{path}.text": None,
                    f"{path}.photo_file_id": None,
                    f"{path}.spoiler": False,
                })
                await cq.answer("Reset to default.", show_alert=True)
                await _show_popup_menu(cq, popup_kind, {}, namespace="rtsave")
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

    if action == "antibypass":
        ab = rt_save.get("anti_bypass", {})
        await _edit(cq, "🎯 ᴀɴᴛɪ-ʙʏᴘᴀss sᴇᴛᴛɪɴɢs (Restrict Save)\n\n"
                        "Strikes 1..(limit-1) show the Bypass Popup with a live warning "
                        "count. The strike that reaches the limit mutes further attempts "
                        "for the mute duration.",
                    antibypass_menu(ab.get("strike_limit", 3), ab.get("mute_seconds", 600), namespace="rtsave"))
        await cq.answer()
        return
    elif action == "antibypass:setstrikes":
        session_manager.set(user_id, "rtsave_set_strike_limit")
        await cq.message.reply_text(
            "🎯 Send the strike limit (whole number, e.g. `3`). The Nth bypass attempt "
            "triggers the mute.",
            reply_markup=cancel_only("generic:cancel"),
        )
        await cq.answer()
        return
    elif action == "antibypass:setmute":
        session_manager.set(user_id, "rtsave_set_mute_seconds")
        await cq.message.reply_text(
            "🔇 Send the mute duration in whole seconds, e.g. `600`.",
            reply_markup=cancel_only("generic:cancel"),
        )
        await cq.answer()
        return

    if action == "setdomain":
        session_manager.set(user_id, "rtsave_set_domain")
        await cq.message.reply_text(
            "🌍 Send the Restrict Save shortener domain, e.g. `example.com` or `https://example.com`. "
            "This is independent from every other Domain in this bot.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "setapi":
        session_manager.set(user_id, "rtsave_set_api")
        await cq.message.reply_text(
            "🔒 Send the Restrict Save shortener API key/token. It will never be shown in full again.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "setmin":
        session_manager.set(user_id, "rtsave_set_min")
        await cq.message.reply_text(
            "🕒 Send the minimum verification time in whole seconds, e.g. `150`.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "setmax":
        session_manager.set(user_id, "rtsave_set_max")
        await cq.message.reply_text(
            "⏰ Send the maximum verification time in whole seconds, e.g. `500`.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "tutorial":
        configured = bool(rt_save.get("tutorial_url"))
        await _edit(cq, "▶️ ᴛᴜᴛᴏʀɪᴀʟ ᴠɪᴅᴇᴏ (Restrict Save)", shortener_tutorial_menu(configured, namespace="rtsave"))
    elif action == "tutorial:set":
        session_manager.set(user_id, "rtsave_set_tutorial")
        await cq.message.reply_text(
            "▶️ Send the tutorial video URL, e.g. `https://example.com/tutorial`.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "tutorial:remove":
        await db.update_settings({"rt_save.tutorial_url": None})
        await cq.answer("Tutorial video removed.", show_alert=True)
        await _edit(cq, "▶️ ᴛᴜᴛᴏʀɪᴀʟ ᴠɪᴅᴇᴏ (Restrict Save)", shortener_tutorial_menu(False, namespace="rtsave"))
        return
    elif action == "duration":
        await _edit(cq, "⏱️ ᴠᴇʀɪғʏ ᴛɪᴍᴇ\n\n"
                        "How long ONE completed verification grants free /rt_save use for.",
                    rt_save_duration_menu(rt_save.get("verify_duration_seconds", 3600)))
    elif action.startswith("setduration:"):
        seconds = int(action.split(":", 1)[1])
        await db.update_settings({"rt_save.verify_duration_seconds": seconds})
        await cq.answer(f"Verify Time set to {format_duration(seconds)}.", show_alert=True)
        await _edit(cq, "⏱️ ᴠᴇʀɪғʏ ᴛɪᴍᴇ\n\n"
                        "How long ONE completed verification grants free /rt_save use for.",
                    rt_save_duration_menu(seconds))
        return
    elif action == "test":
        from triss.services import shortener as shortener_service
        await cq.answer("Testing...")
        test_url = await shortener_service.generate_short_link(rt_save, "https://telegram.org")
        if test_url:
            await client.send_message(user_id, f"🧪 Shortener test succeeded:\n{test_url}")
        else:
            await client.send_message(
                user_id,
                "🧪 Shortener test failed — check Domain and API key are correct and the provider is reachable."
            )
        return
    elif action == "toggleshortener":
        new_value = not rt_save.get("shortener_enabled", True)
        if new_value and (not rt_save.get("domain") or not rt_save.get("api_key")):
            await cq.answer("Set a Domain and API key before enabling the Shortener step.", show_alert=True)
            return
        await db.update_settings({"rt_save.shortener_enabled": new_value})
        await cq.answer(f"Shortener step {'enabled' if new_value else 'disabled'}.", show_alert=True)
        settings = await db.get_settings()
        await _edit(cq, "🔓 <b>Restrict Save</b>", rt_save_menu(settings.get("rt_save", {})))
        return
    elif action == "toggle":
        new_value = not rt_save.get("enabled", False)
        await db.update_settings({"rt_save.enabled": new_value})
        await cq.answer(f"Public Use {'enabled' if new_value else 'disabled'}.", show_alert=True)
        settings = await db.get_settings()
        await _edit(cq, "🔓 <b>Restrict Save</b>", rt_save_menu(settings.get("rt_save", {})))
        return
    await cq.answer()


_CHANNEL_LABELS = {"storage": "🏪 sᴛᴏʀᴇ ᴄʜᴀɴɴᴇʟ", "log": "🧾 ʟᴏɢ ᴄʜᴀɴɴᴇʟ"}


async def _sync_log_channel_cache(kind: str) -> None:
    """Keeps triss.utils.restrict's log-channel cache in sync the instant
    the active Log Channel changes, so Restrict Content immediately stops
    (or starts) exempting the right channel - no restart needed."""
    if kind == "log":
        from triss.utils.restrict import set_log_channel_id_cache
        set_log_channel_id_cache(await db.get_active_channel_id("log"))


async def _channel_entries(kind: str) -> tuple[list[dict], int | None]:
    """Saved list (plus the currently-active one if it came from the
    env/older single-channel setting and isn't in the list yet)."""
    channels = await db.list_channels(kind)
    active_id = await db.get_active_channel_id(kind)
    if active_id and all(c["chat_id"] != active_id for c in channels):
        channels = [{"chat_id": active_id, "title": f"Current ({active_id})"}] + channels
    return channels, active_id


async def _channel_status_text(kind: str) -> str:
    channels, active_id = await _channel_entries(kind)
    active = next((c for c in channels if c["chat_id"] == active_id), None)
    text = f"{_CHANNEL_LABELS[kind]}\n\n"
    if active:
        text += f"Active: ✅ {active.get('title') or active['chat_id']}\n"
    else:
        text += "Active: ❌ Not set\n"
    text += f"Saved: {len(channels)}"
    return text


@app.on_callback_query(filters.regex(r"^chan:"))
async def channel_router(client, cq: CallbackQuery) -> None:
    """Store Channel / Log Channel manager: chan:<storage|log>:<add|list|use:ID|rm:ID>."""
    if await deny_if_not_owner(cq):
        return
    parts = cq.data.split(":")
    kind, action = parts[1], parts[2]
    if kind not in _CHANNEL_LABELS:
        await cq.answer()
        return
    user_id = cq.from_user.id
    back = "settings:storechannel" if kind == "storage" else "settings:logchannel"

    if action == "add":
        session_manager.set(user_id, "channel_add", {"kind": kind})
        await cq.message.reply_text(
            f"{_CHANNEL_LABELS[kind]}\n\nForward any message from the channel, or send its numeric ID. "
            "The bot must be an admin there.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action in ("list", "use", "rm"):
        if action == "use" and len(parts) > 3:
            await db.set_active_channel(kind, int(parts[3]))
            await _sync_log_channel_cache(kind)
            await cq.answer("✅ Set as active.")
        elif action == "rm" and len(parts) > 3:
            chat_id = int(parts[3])
            await db.remove_channel(kind, chat_id)
            if await db.get_active_channel_id(kind) == chat_id:
                await db.set_active_channel(kind, None)
                await _sync_log_channel_cache(kind)
            await cq.answer("Removed.")
        channels, active_id = await _channel_entries(kind)
        await _edit(cq, await _channel_status_text(kind) + "\n\nTap a channel to make it active.",
                    channel_list_menu(kind, channels, active_id))
        return
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
    patch = {"welcome.photo_file_id": message.photo.file_id, "welcome.media_type": "photo"}
    if message.caption:
        patch["welcome.text"] = message.caption.html
    await db.update_settings(patch)
    await message.reply_text("✅ Welcome photo updated.")


@app.on_message(filters.private & session_is("welcome_set_video") & filters.video)
async def capture_welcome_video(client, message: Message) -> None:
    session_manager.clear(message.from_user.id)
    patch = {"welcome.video_file_id": message.video.file_id, "welcome.media_type": "video"}
    if message.caption:
        patch["welcome.text"] = message.caption.html
    await db.update_settings(patch)
    await message.reply_text("✅ Welcome video updated.")


@app.on_message(filters.private & session_is("welcome_set_gif") & filters.animation)
async def capture_welcome_gif(client, message: Message) -> None:
    session_manager.clear(message.from_user.id)
    patch = {"welcome.animation_file_id": message.animation.file_id, "welcome.media_type": "animation"}
    if message.caption:
        patch["welcome.text"] = message.caption.html
    await db.update_settings(patch)
    await message.reply_text("✅ Welcome GIF updated.")


@app.on_message(filters.private & session_is("welcome_set_text") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_welcome_text(client, message: Message) -> None:
    session_manager.clear(message.from_user.id)
    # `message.text` is the plain, entity-stripped string — any bold/italic/
    # etc. the owner applied while typing is lost before it ever reaches the
    # database. `.html` renders those entities back into HTML tags
    # (<b>, <i>, ...) so it displays correctly when this text is sent back
    # out later — the bot's parse_mode is pinned to HTML (see triss/bot.py)
    # specifically so this round-trip is reliable for any text/symbols the
    # owner pastes in, not just Telegram's own formatting toolbar output.
    await db.update_settings({"welcome.text": message.text.html})
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
        await message.reply_text("⚠️ Invalid duration. Use formats like <code>10s</code>, <code>5m</code>, <code>2h</code>.")
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
    instantly. triss.services.forcesub's chat-join-request handler
    records (does NOT auto-approve) those requests — the request stays
    genuinely pending for a real admin to act on, and Force Sub treats
    the submitted request itself as satisfying a "request"-mode entry;
    see the module docstring in triss/services/forcesub.py for the full
    explanation of why approval is intentionally never automated."""
    try:
        if join_mode == "request":
            link_obj = await client.create_chat_invite_link(chat_id, creates_join_request=True)
            return link_obj.invite_link
        return await client.export_chat_invite_link(chat_id)
    except RPCError:
        logger.info("Could not create/export invite link for %s (bot may not be admin yet).", chat_id)
        return None


async def _bot_admin_status(client, chat_id: int) -> Optional[ChatMemberStatus]:
    """Returns the bot's own membership status in `chat_id`, or None if it
    couldn't be determined (chat unreachable, bot not even a member, etc).
    BUG FIX: the old code never checked this before saving a Force Sub
    entry — if the bot wasn't an admin, export_chat_invite_link/
    create_chat_invite_link would silently fail (caught, returns None),
    the entry got saved anyway with NO invite link, and later
    get_chat_member calls in triss.services.forcesub would ALSO fail and
    fail-open (treat the requirement as satisfied for everyone) — so
    Force Sub silently did nothing for that channel, with no error ever
    shown to the owner. Checking admin status up front, before saving,
    and rejecting clearly if it fails is the actual fix."""
    try:
        me = await client.get_chat_member(chat_id, "me")
        return me.status
    except RPCError:
        return None


_ADMIN_STATUSES = {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}


@app.on_message(filters.private & session_is("forcesub_add_channel") & ~filters.command(_ADMIN_COMMANDS))
async def capture_forcesub_channel(client, message: Message) -> None:
    session = session_manager.get(message.from_user.id)
    join_mode = (session.data.get("join_mode") if session else None) or "normal"
    chat_id, title = await _resolve_chat_from_message(client, message, "channel")
    if chat_id is None:
        await message.reply_text("⚠️ Send a forwarded message from the channel, or its numeric ID.")
        return
    status = await _bot_admin_status(client, chat_id)
    if status not in _ADMIN_STATUSES:
        # Do NOT clear the session — let the owner fix admin rights and
        # resend the same forward/ID without starting over from the menu.
        await message.reply_text(
            "⚠️ I'm not an admin in that channel (or couldn't check).\n\n"
            "Please add me as an <b>admin</b> in the channel — with at least "
            "'Invite Users via Link' permission — then send the channel here again."
        )
        return
    session_manager.clear(message.from_user.id)
    invite_link = await _export_invite_link(client, chat_id, join_mode)
    if invite_link is None:
        await message.reply_text(
            "⚠️ I'm an admin there, but couldn't create an invite link — check my "
            "'Invite Users via Link' permission and try Add Channel again."
        )
        return
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
    status = await _bot_admin_status(client, chat_id)
    if status not in _ADMIN_STATUSES:
        await message.reply_text(
            "⚠️ I'm not an admin in that group (or couldn't check).\n\n"
            "Please add me as an <b>admin</b> in the group — with at least "
            "'Invite Users via Link' permission — then send the group here again."
        )
        return
    session_manager.clear(message.from_user.id)
    invite_link = await _export_invite_link(client, chat_id, join_mode)
    if invite_link is None:
        await message.reply_text(
            "⚠️ I'm an admin there, but couldn't create an invite link — check my "
            "'Invite Users via Link' permission and try Add Group again."
        )
        return
    ok = await db.add_force_sub("group", chat_id, title, invite_link, join_mode=join_mode)
    await message.reply_text("✅ Group added to Force Sub." if ok else "⚠️ That group is already configured.")


@app.on_message(filters.private & session_is("forcesub_set_message_text") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_forcesub_message_text(client, message: Message) -> None:
    session_manager.clear(message.from_user.id)
    await db.update_settings({"force_sub_message.text": message.text.html})
    await message.reply_text("✅ Force Sub message text updated.")


@app.on_message(filters.private & session_is("forcesub_set_message_photo") & filters.photo)
async def capture_forcesub_message_photo(client, message: Message) -> None:
    session_manager.clear(message.from_user.id)
    patch = {"force_sub_message.photo_file_id": message.photo.file_id}
    if message.caption:
        patch["force_sub_message.text"] = message.caption.html
    await db.update_settings(patch)
    await message.reply_text("✅ Force Sub message photo updated.")


@app.on_message(filters.private & session_is("forcesub_set_button_text") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_forcesub_button_text(client, message: Message) -> None:
    session = session_manager.get(message.from_user.id)
    session_manager.clear(message.from_user.id)
    if session is None:
        return
    kind = session.data.get("kind")
    chat_id = session.data.get("chat_id")
    new_text = None if message.text.strip().lower() == "reset" else message.text.html
    ok = await db.set_force_sub_button_text(kind, chat_id, new_text)
    if not ok:
        await message.reply_text("⚠️ That entry no longer exists.")
        return
    await message.reply_text("✅ Button label reset to default." if new_text is None else "✅ Button label updated.")


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


@app.on_message(filters.private & session_is("channel_add") & ~filters.command(_ADMIN_COMMANDS))
async def capture_channel_add(client, message: Message) -> None:
    session = session_manager.get(message.from_user.id)
    kind = (session.data.get("kind") if session else None) or "storage"
    chat_id, title = await _resolve_chat_from_message(client, message, kind)
    if chat_id is None:
        await message.reply_text("⚠️ Forward a message from the channel, or send its numeric ID.")
        return
    status = await _bot_admin_status(client, chat_id)
    if status not in _ADMIN_STATUSES:
        # keep the session so the owner can fix admin rights and resend
        await message.reply_text("⚠️ I'm not an admin in that channel (or couldn't check). Add me as admin, then send it again.")
        return
    session_manager.clear(message.from_user.id)
    first = not await db.list_channels(kind)
    added = await db.add_channel(kind, chat_id, title or str(chat_id))
    if added and (first or not await db.get_active_channel_id(kind)):
        await db.set_active_channel(kind, chat_id)
        await _sync_log_channel_cache(kind)
        await message.reply_text(f"✅ {title} added and set as active.")
    elif added:
        await message.reply_text(f"✅ {title} added. Open List and tap it to make it active.")
    else:
        await message.reply_text("⚠️ That channel is already in the list.")


# ---------------------------------------------------------------------------
# Shortener submenu
# ---------------------------------------------------------------------------

def _shortener_status_text(shortener_settings: dict) -> str:
    status = "ON" if shortener_settings.get("enabled") else "OFF"
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
        f"API: <code>{api_display}</code>\n"
        f"Minimum: {minimum}s\n"
        f"Maximum: {maximum}s\n"
        f"Tutorial: {tutorial}\n"
        f"Anti-Bypass: {strikes} strikes / {mute_s}s mute\n\n"
        "ℹ️ This checks the *time window* between link creation and the user "
        "returning — it does not confirm the shortener page/ad was actually "
        "completed. Treat it as a delay gate, not a completion proof."
    )


def _system_access_status_text(sa_settings: dict) -> str:
    status = "ON" if sa_settings.get("enabled") else "OFF"
    domain = sa_settings.get("domain") or "Not set"
    api_display = mask_secret(sa_settings.get("api_key"))
    minimum = sa_settings.get("minimum_seconds", 0)
    maximum = sa_settings.get("maximum_seconds", 0)
    tutorial = "Configured" if sa_settings.get("tutorial_url") else "Not set"
    anti_bypass = sa_settings.get("anti_bypass", {})
    strikes = anti_bypass.get("strike_limit", 3)
    mute_s = anti_bypass.get("mute_seconds", 600)
    duration = sa_settings.get("access_duration_seconds", 21600)
    return (
        "♻️ sʏsᴛᴇᴍ ᴀᴄᴄᴇss\n\n"
        f"Status: {status}\n"
        f"Domain: {domain}\n"
        f"API: <code>{api_display}</code>\n"
        f"Minimum: {minimum}s\n"
        f"Maximum: {maximum}s\n"
        f"Verify Time (access duration): {format_duration(duration)}\n"
        f"Tutorial: {tutorial}\n"
        f"Anti-Bypass: {strikes} strikes / {mute_s}s mute\n\n"
        "ℹ️ One completed verification grants the user unlimited use of "
        "EVERY link in the bot for the Verify Time above, instead of "
        "gating just the one link they opened (that's Old Method / "
        "🌐 Shortener). Force Sub is separate and is never skipped by an "
        "active System Access window.\n\n"
        "⚠️ Old Method and System Access can never both be ON — enabling "
        "one here automatically turns the other off."
    )


_POPUP_KINDS = {
    "verifymsg": ("verify_message", SHORTENER_VERIFY_TEXT, "💬 Verify Popup"),
    "bypassmsg": ("bypass_message", SHORTENER_BYPASS_TEXT, "🚨 Bypass Popup"),
    "mutedmsg": ("muted_message", SHORTENER_MUTED_TEXT, "🔇 Muted Popup"),
}


async def _show_popup_menu(cq: CallbackQuery, kind: str, popup_settings: dict, namespace: str = "shortener") -> None:
    field_name, _default, title = _POPUP_KINDS[kind]
    has_photo = bool(popup_settings.get("photo_file_id"))
    spoiler = bool(popup_settings.get("spoiler"))
    custom = "Custom text set" if popup_settings.get("text") else "Using default text"
    await _edit(
        cq,
        f"{title}\n\n{custom}\nPhoto: {'✅ Set' if has_photo else '❌ Not set'}\n"
        f"Spoiler: {'ON' if spoiler else 'OFF'}",
        shortener_message_menu(kind, has_photo, spoiler, namespace=namespace),
    )


@app.on_callback_query(filters.regex(r"^shortener:"))
async def shortener_router(client, cq: CallbackQuery) -> None:
    action = cq.data.split(":", 1)[1]
    user_id = cq.from_user.id

    if action.startswith("retry:"):
        # Any user (not just owner) can retry their own verification.
        # callback_data is "retry:<mode_code>:<access_token>" - mode_code
        # ("pl"/"sa") reflects which mode was active when the button was
        # built, but the retry always re-derives the CURRENTLY active mode
        # fresh from settings (same "trust live config, not history"
        # philosophy the old-method-only version of this code already
        # used for its enabled/disabled check below) - so if the owner
        # switched modes since the original attempt, retry correctly
        # follows the new mode instead of a stale button.
        from triss.services import shortener as shortener_service

        rest = action.split(":", 1)[1]  # "<mode_code>:<access_token>"
        _mode_code, _, access_token = rest.partition(":")
        if not access_token and _mode_code != "rt":
            # Backward-compat: an old button from before mode_code existed
            # ("retry:<access_token>" with no mode segment).
            access_token = _mode_code

        if _mode_code == "rt":
            # 🔓 rt_save has no content link at all - none of the
            # link_doc/per_link/system_access logic below applies. Just
            # re-launch a fresh rt_save verification, same as /rt_save.
            settings = await db.get_settings()
            if not settings.get("rt_save", {}).get("enabled"):
                await cq.answer("This feature is currently disabled.", show_alert=True)
                return
            await _start_new_verification_message(
                client, cq.message, user_id, "", settings.get("rt_save", {}), mode="rt_save",
            )
            await cq.answer()
            return

        settings = await db.get_settings()
        shortener_settings = settings.get("shortener", {})
        system_access_settings = shortener_settings.get("system_access", {})
        active_mode = "system_access" if system_access_settings.get("enabled") else (
            "per_link" if shortener_settings.get("enabled") else None
        )
        active_settings = system_access_settings if active_mode == "system_access" else shortener_settings

        anti_bypass = active_settings.get("anti_bypass", {}) if active_mode else shortener_settings.get("anti_bypass", {})
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
                fs_text = settings.get("force_sub_message", {}).get("text") or FORCE_SUB_TEXT
                await cq.message.edit_text(fs_text, reply_markup=force_sub_user_keyboard(entries))
                await db.set_pending_access(user_id, access_token)
                await cq.answer()
                return

        # Existing System Access grant takes priority over starting a new
        # verification at all - matches continue_after_force_sub's own
        # ordering (see triss.handlers.start).
        if active_mode == "system_access" and await db.has_active_system_access(user_id):
            await continue_after_force_sub(client, cq.message, user_id, access_token, settings)
            await cq.answer()
            return

        if active_mode is None:
            # Both Old Method and System Access are off - just deliver.
            await continue_after_force_sub(client, cq.message, user_id, access_token, settings)
            await cq.answer()
            return

        await _start_new_verification_message(
            client, cq.message, user_id, access_token, active_settings, mode=active_mode
        )
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
        patch = {"shortener.enabled": new_value}
        disabled_system_access = False
        if new_value and shortener_settings.get("system_access", {}).get("enabled"):
            # Mutual exclusion: Old Method and System Access can never both
            # be ON. Enabling this one silently turns the other off.
            patch["shortener.system_access.enabled"] = False
            disabled_system_access = True
        await db.update_settings(patch)
        settings = await db.get_settings()
        await _edit(cq, _shortener_status_text(settings["shortener"]), shortener_menu(new_value))
        note = " (System Access was turned off, since both can't be on together.)" if disabled_system_access else ""
        await cq.answer(f"Shortener {'enabled' if new_value else 'disabled'}.{note}", show_alert=True)
        return
    await cq.answer()


@app.on_callback_query(filters.regex(r"^restrict:"))
async def restrict_content_router(client, cq: CallbackQuery) -> None:
    if await deny_if_not_owner(cq):
        return
    from triss.utils.restrict import set_restrict_content_cache

    settings = await db.get_settings()
    new_value = not settings.get("restrict_content", False)
    await db.update_settings({"restrict_content": new_value})
    set_restrict_content_cache(new_value)  # live immediately, no restart needed
    settings = await db.get_settings()
    await _edit(cq, f"⚙️ ᴛʀɪss sᴇᴛᴛɪɴɢs\n\n👥 Total bot users: {await db.count_users():,}",
                settings_main_menu(settings))
    await cq.answer(
        f"🔒 Restrict Content {'ON' if new_value else '🔓 OFF'} — every message this bot "
        f"sends {'can no longer' if new_value else 'can now'} be forwarded or saved.",
        show_alert=True,
    )


# ---------------------------------------------------------------------------
# ♻️ System Access (New Method) — mirrors the shortener_router logic above
# exactly, but reads/writes shortener.system_access.* and enforces mutual
# exclusion with Old Method on its own toggle too.
# ---------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^sysaccess:"))
async def system_access_router(client, cq: CallbackQuery) -> None:
    if await deny_if_not_owner(cq):
        return
    action = cq.data.split(":", 1)[1]
    user_id = cq.from_user.id

    settings = await db.get_settings()
    sa_settings = settings.get("shortener", {}).get("system_access", {})

    # --- Customizable popups: verifymsg / bypassmsg / mutedmsg ---
    for popup_kind, (field_name, default_text, _title) in _POPUP_KINDS.items():
        if action == popup_kind:
            await _show_popup_menu(cq, popup_kind, sa_settings.get(field_name, {}), namespace="sysaccess")
            await cq.answer()
            return
        if action.startswith(f"{popup_kind}:"):
            sub_action = action.split(":", 1)[1]
            popup_settings = sa_settings.get(field_name, {})
            path = f"shortener.system_access.{field_name}"
            if sub_action == "settext":
                session_manager.set(user_id, f"sysaccess_set_{popup_kind}_text")
                await cq.message.reply_text(
                    "💬 Send the new popup text now.",
                    reply_markup=cancel_only("generic:cancel"),
                )
            elif sub_action == "setphoto":
                session_manager.set(user_id, f"sysaccess_set_{popup_kind}_photo")
                await cq.message.reply_text(
                    "📸 Send the photo now. If you add a caption, it becomes the popup text too.",
                    reply_markup=cancel_only("generic:cancel"),
                )
            elif sub_action == "removephoto":
                await db.update_settings({f"{path}.photo_file_id": None})
                await cq.answer("Photo removed.", show_alert=True)
                settings = await db.get_settings()
                sa_settings = settings["shortener"]["system_access"]
                await _show_popup_menu(cq, popup_kind, sa_settings.get(field_name, {}), namespace="sysaccess")
                return
            elif sub_action == "spoiler":
                new_value = not popup_settings.get("spoiler", False)
                await db.update_settings({f"{path}.spoiler": new_value})
                await cq.answer(f"Spoiler {'enabled' if new_value else 'disabled'}.", show_alert=True)
                settings = await db.get_settings()
                sa_settings = settings["shortener"]["system_access"]
                await _show_popup_menu(cq, popup_kind, sa_settings.get(field_name, {}), namespace="sysaccess")
                return
            elif sub_action == "reset":
                await db.update_settings({
                    f"{path}.text": None,
                    f"{path}.photo_file_id": None,
                    f"{path}.spoiler": False,
                })
                await cq.answer("Reset to default.", show_alert=True)
                await _show_popup_menu(cq, popup_kind, {}, namespace="sysaccess")
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
        ab = sa_settings.get("anti_bypass", {})
        await _edit(cq, "🎯 ᴀɴᴛɪ-ʙʏᴘᴀss sᴇᴛᴛɪɴɢs (System Access)\n\n"
                        "Strikes 1..(limit-1) show the Bypass Popup with a live warning "
                        "count. The strike that reaches the limit shows the Muted Popup "
                        "instead and blocks further attempts for the mute duration "
                        "(never shown to the user).",
                    antibypass_menu(ab.get("strike_limit", 3), ab.get("mute_seconds", 600), namespace="sysaccess"))
        await cq.answer()
        return
    elif action == "antibypass:setstrikes":
        session_manager.set(user_id, "sysaccess_set_strike_limit")
        await cq.message.reply_text(
            "🎯 Send the strike limit (whole number, e.g. `3`). The Nth bypass attempt "
            "triggers the mute.",
            reply_markup=cancel_only("generic:cancel"),
        )
        await cq.answer()
        return
    elif action == "antibypass:setmute":
        session_manager.set(user_id, "sysaccess_set_mute_seconds")
        await cq.message.reply_text(
            "🔇 Send the mute duration in whole seconds, e.g. `600`. This is never shown "
            "to the user.",
            reply_markup=cancel_only("generic:cancel"),
        )
        await cq.answer()
        return

    if action == "setdomain":
        session_manager.set(user_id, "sysaccess_set_domain")
        await cq.message.reply_text(
            "🌍 Send the System Access shortener domain, e.g. `example.com` or `https://example.com`. "
            "This is independent from Old Method's Domain.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "setapi":
        session_manager.set(user_id, "sysaccess_set_api")
        await cq.message.reply_text(
            "🔒 Send the System Access shortener API key/token. It will never be shown in full again.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "setmin":
        session_manager.set(user_id, "sysaccess_set_min")
        await cq.message.reply_text(
            "🕒 Send the minimum verification time in whole seconds, e.g. `150`.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "setmax":
        session_manager.set(user_id, "sysaccess_set_max")
        await cq.message.reply_text(
            "⏰ Send the maximum verification time in whole seconds, e.g. `500`.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "tutorial":
        configured = bool(sa_settings.get("tutorial_url"))
        await _edit(cq, "▶️ ᴛᴜᴛᴏʀɪᴀʟ ᴠɪᴅᴇᴏ (System Access)", shortener_tutorial_menu(configured, namespace="sysaccess"))
    elif action == "tutorial:set":
        session_manager.set(user_id, "sysaccess_set_tutorial")
        await cq.message.reply_text(
            "▶️ Send the tutorial video URL, e.g. `https://example.com/tutorial`.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "tutorial:remove":
        await db.update_settings({"shortener.system_access.tutorial_url": None})
        await cq.answer("Tutorial video removed.", show_alert=True)
        await _edit(cq, "▶️ ᴛᴜᴛᴏʀɪᴀʟ ᴠɪᴅᴇᴏ (System Access)", shortener_tutorial_menu(False, namespace="sysaccess"))
        return
    elif action == "duration":
        await _edit(cq, "⏱️ ᴠᴇʀɪғʏ ᴛɪᴍᴇ (ᴀᴄᴄᴇss ᴅᴜʀᴀᴛɪᴏɴ)\n\n"
                        "How long ONE completed verification grants unlimited bot access for.",
                    system_access_duration_menu(sa_settings.get("access_duration_seconds", 21600)))
    elif action.startswith("setduration:"):
        seconds = int(action.split(":", 1)[1])
        await db.update_settings({"shortener.system_access.access_duration_seconds": seconds})
        await cq.answer(f"Verify Time set to {format_duration(seconds)}.", show_alert=True)
        await _edit(cq, "⏱️ ᴠᴇʀɪғʏ ᴛɪᴍᴇ (ᴀᴄᴄᴇss ᴅᴜʀᴀᴛɪᴏɴ)\n\n"
                        "How long ONE completed verification grants unlimited bot access for.",
                    system_access_duration_menu(seconds))
        return
    elif action == "toggle":
        if not sa_settings.get("enabled"):
            if not sa_settings.get("domain") or not sa_settings.get("api_key"):
                await cq.answer("Set a Domain and API key before enabling System Access.", show_alert=True)
                return
            if sa_settings.get("maximum_seconds", 0) < sa_settings.get("minimum_seconds", 0):
                await cq.answer("Maximum time must be >= Minimum time before enabling.", show_alert=True)
                return
        new_value = not sa_settings.get("enabled", False)
        patch = {"shortener.system_access.enabled": new_value}
        disabled_old_method = False
        if new_value and settings.get("shortener", {}).get("enabled"):
            # Mutual exclusion, other direction.
            patch["shortener.enabled"] = False
            disabled_old_method = True
        await db.update_settings(patch)
        settings = await db.get_settings()
        await _edit(cq, _system_access_status_text(settings["shortener"]["system_access"]),
                    system_access_menu(settings["shortener"]["system_access"]))
        note = " (Old Method Shortener was turned off, since both can't be on together.)" if disabled_old_method else ""
        await cq.answer(f"System Access {'enabled' if new_value else 'disabled'}.{note}", show_alert=True)
        return
    await cq.answer()

async def _start_new_verification_message(client, message: Message, user_id: int, access_token: str,
                                           shortener_settings: dict, mode: str = "per_link") -> None:
    """Used by the 'Try Again' retry path: creates a brand-new verification
    session (never reusing the old one) and shows the verification prompt
    again, exactly like a first-time access. `mode` ("per_link" or
    "system_access") is forwarded so the retried session is tagged the
    same way a fresh access under the currently-active mode would be."""
    from triss.services import shortener as shortener_service
    from triss.utils.formatting import SHORTENER_VERIFY_TEXT, SHORTENER_UNAVAILABLE_TEXT
    from triss.utils.keyboards import shortener_verification_keyboard
    from triss.handlers.start import _send_customizable_popup

    username = getattr(client, "username", None) or (await client.get_me()).username
    _session, short_url = await shortener_service.start_new_verification(
        username, user_id, access_token, shortener_settings, mode=mode
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
        await message.reply_text("⚠️ That doesn't look like a valid domain. Try <code>example.com</code>.")
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"shortener.domain": domain})
    await message.reply_text(f"✅ Shortener domain set to <code>{domain}</code>.")


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
    await message.reply_text(f"✅ Shortener API key updated: <code>{mask_secret(api_key)}</code>")


@app.on_message(filters.private & session_is("shortener_set_min") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_shortener_min(client, message: Message) -> None:
    text = message.text.strip()
    if not text.isdigit():
        await message.reply_text("⚠️ Send a whole number of seconds, e.g. <code>150</code>.")
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
        await message.reply_text("⚠️ Send a whole number of seconds, e.g. <code>500</code>.")
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
        await message.reply_text("⚠️ Send a whole number of 1 or more, e.g. <code>3</code>.")
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"shortener.anti_bypass.strike_limit": int(text)})
    await message.reply_text(f"✅ Strike limit set to {text}.")


@app.on_message(filters.private & session_is("shortener_set_mute_seconds") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_mute_seconds(client, message: Message) -> None:
    text = message.text.strip()
    if not text.isdigit() or int(text) < 1:
        await message.reply_text("⚠️ Send a whole number of seconds, e.g. <code>600</code>.")
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"shortener.anti_bypass.mute_seconds": int(text)})
    await message.reply_text(f"✅ Mute duration set to {text}s (never shown to the user).")


# ---------------------------------------------------------------------------
# System Access (New Method) — same capture handlers as Old Method above,
# but writing to shortener.system_access.* and validating min/max against
# System Access's OWN maximum/minimum (fully independent config, per spec).
# ---------------------------------------------------------------------------

@app.on_message(filters.private & session_is("sysaccess_set_domain") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_sysaccess_domain(client, message: Message) -> None:
    domain = normalize_shortener_domain(message.text)
    if domain is None:
        await message.reply_text("⚠️ That doesn't look like a valid domain. Try <code>example.com</code>.")
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"shortener.system_access.domain": domain})
    await message.reply_text(f"✅ System Access domain set to <code>{domain}</code>.")


@app.on_message(filters.private & session_is("sysaccess_set_api") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_sysaccess_api(client, message: Message) -> None:
    api_key = message.text.strip()
    if not is_valid_api_key(api_key):
        await message.reply_text("⚠️ That doesn't look like a valid API key (no spaces, 3-256 characters).")
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"shortener.system_access.api_key": api_key})
    try:
        await message.delete()
    except RPCError:
        pass
    await message.reply_text(f"✅ System Access API key updated: <code>{mask_secret(api_key)}</code>")


@app.on_message(filters.private & session_is("sysaccess_set_min") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_sysaccess_min(client, message: Message) -> None:
    text = message.text.strip()
    if not text.isdigit():
        await message.reply_text("⚠️ Send a whole number of seconds, e.g. <code>150</code>.")
        return
    seconds = int(text)
    if seconds < 0 or seconds > 24 * 3600:
        await message.reply_text("⚠️ Please choose a value between 0 and 86400 seconds.")
        return
    settings = await db.get_settings()
    maximum = settings.get("shortener", {}).get("system_access", {}).get("maximum_seconds", 0)
    if seconds > maximum:
        await message.reply_text(
            f"⚠️ Minimum ({seconds}s) cannot be greater than the current Maximum ({maximum}s). "
            f"Set Maximum first, or choose a smaller Minimum."
        )
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"shortener.system_access.minimum_seconds": seconds})
    await message.reply_text(f"✅ Minimum verification time set to {seconds}s.")


@app.on_message(filters.private & session_is("sysaccess_set_max") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_sysaccess_max(client, message: Message) -> None:
    text = message.text.strip()
    if not text.isdigit():
        await message.reply_text("⚠️ Send a whole number of seconds, e.g. <code>500</code>.")
        return
    seconds = int(text)
    if seconds <= 0 or seconds > 24 * 3600:
        await message.reply_text("⚠️ Please choose a value between 1 and 86400 seconds.")
        return
    settings = await db.get_settings()
    minimum = settings.get("shortener", {}).get("system_access", {}).get("minimum_seconds", 0)
    if seconds < minimum:
        await message.reply_text(
            f"⚠️ Maximum ({seconds}s) cannot be lower than the current Minimum ({minimum}s)."
        )
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"shortener.system_access.maximum_seconds": seconds})
    await message.reply_text(f"✅ Maximum verification time set to {seconds}s.")


@app.on_message(filters.private & session_is("sysaccess_set_tutorial") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_sysaccess_tutorial(client, message: Message) -> None:
    url = message.text.strip()
    if not is_valid_url(url):
        await message.reply_text("⚠️ Send a valid URL starting with http:// or https://.")
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"shortener.system_access.tutorial_url": url})
    await message.reply_text("✅ Tutorial video URL set.")


@app.on_message(filters.private & session_is("sysaccess_set_strike_limit") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_sysaccess_strike_limit(client, message: Message) -> None:
    text = message.text.strip()
    if not text.isdigit() or int(text) < 1:
        await message.reply_text("⚠️ Send a whole number of 1 or more, e.g. <code>3</code>.")
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"shortener.system_access.anti_bypass.strike_limit": int(text)})
    await message.reply_text(f"✅ Strike limit set to {text}.")


@app.on_message(filters.private & session_is("sysaccess_set_mute_seconds") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_sysaccess_mute_seconds(client, message: Message) -> None:
    text = message.text.strip()
    if not text.isdigit() or int(text) < 1:
        await message.reply_text("⚠️ Send a whole number of seconds, e.g. <code>600</code>.")
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"shortener.system_access.anti_bypass.mute_seconds": int(text)})
    await message.reply_text(f"✅ Mute duration set to {text}s (never shown to the user).")


# --- 🔓 Restrict Save - its own Domain/API/Min-Max/Anti-Bypass, mirroring
# the System Access capture handlers above exactly, settings path "rt_save.*" ---

@app.on_message(filters.private & session_is("rtsave_set_domain") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_rtsave_domain(client, message: Message) -> None:
    domain = normalize_shortener_domain(message.text)
    if domain is None:
        await message.reply_text("⚠️ That doesn't look like a valid domain. Try <code>example.com</code>.")
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"rt_save.domain": domain})
    await message.reply_text(f"✅ Restrict Save domain set to <code>{domain}</code>.")


@app.on_message(filters.private & session_is("rtsave_set_api") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_rtsave_api(client, message: Message) -> None:
    api_key = message.text.strip()
    if not is_valid_api_key(api_key):
        await message.reply_text("⚠️ That doesn't look like a valid API key (no spaces, 3-256 characters).")
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"rt_save.api_key": api_key})
    try:
        await message.delete()
    except RPCError:
        pass
    await message.reply_text("✅ Restrict Save API key set.")


@app.on_message(filters.private & session_is("rtsave_set_min") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_rtsave_min(client, message: Message) -> None:
    text = message.text.strip()
    if not text.isdigit():
        await message.reply_text("⚠️ Send a whole number of seconds, e.g. <code>150</code>.")
        return
    seconds = int(text)
    if seconds < 0 or seconds > 24 * 3600:
        await message.reply_text("⚠️ Please choose a value between 0 and 86400 seconds.")
        return
    settings = await db.get_settings()
    maximum = settings.get("rt_save", {}).get("maximum_seconds", 0)
    if seconds > maximum:
        await message.reply_text(
            f"⚠️ Minimum ({seconds}s) cannot be greater than the current Maximum ({maximum}s). "
            f"Set Maximum first, or choose a smaller Minimum."
        )
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"rt_save.minimum_seconds": seconds})
    await message.reply_text(f"✅ Minimum verification time set to {seconds}s.")


@app.on_message(filters.private & session_is("rtsave_set_max") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_rtsave_max(client, message: Message) -> None:
    text = message.text.strip()
    if not text.isdigit():
        await message.reply_text("⚠️ Send a whole number of seconds, e.g. <code>500</code>.")
        return
    seconds = int(text)
    if seconds <= 0 or seconds > 24 * 3600:
        await message.reply_text("⚠️ Please choose a value between 1 and 86400 seconds.")
        return
    settings = await db.get_settings()
    minimum = settings.get("rt_save", {}).get("minimum_seconds", 0)
    if seconds < minimum:
        await message.reply_text(
            f"⚠️ Maximum ({seconds}s) cannot be lower than the current Minimum ({minimum}s)."
        )
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"rt_save.maximum_seconds": seconds})
    await message.reply_text(f"✅ Maximum verification time set to {seconds}s.")


@app.on_message(filters.private & session_is("rtsave_set_tutorial") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_rtsave_tutorial(client, message: Message) -> None:
    url = message.text.strip()
    if not is_valid_url(url):
        await message.reply_text("⚠️ Send a valid URL starting with http:// or https://.")
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"rt_save.tutorial_url": url})
    await message.reply_text("✅ Tutorial video URL set.")


@app.on_message(filters.private & session_is("rtsave_set_strike_limit") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_rtsave_strike_limit(client, message: Message) -> None:
    text = message.text.strip()
    if not text.isdigit() or int(text) < 1:
        await message.reply_text("⚠️ Send a whole number of 1 or more, e.g. <code>3</code>.")
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"rt_save.anti_bypass.strike_limit": int(text)})
    await message.reply_text(f"✅ Strike limit set to {text}.")


@app.on_message(filters.private & session_is("rtsave_set_mute_seconds") & filters.text & ~filters.command(_ADMIN_COMMANDS))
async def capture_rtsave_mute_seconds(client, message: Message) -> None:
    text = message.text.strip()
    if not text.isdigit() or int(text) < 1:
        await message.reply_text("⚠️ Send a whole number of seconds, e.g. <code>600</code>.")
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({"rt_save.anti_bypass.mute_seconds": int(text)})
    await message.reply_text(f"✅ Mute duration set to {text}s (never shown to the user).")


# ---------------------------------------------------------------------------
# Customizable popups (verify / bypass / muted) — text & photo capture
# handlers, generated once per (popup kind, namespace) pair to avoid
# repeating the same handler by hand for Old Method AND System Access.
# ---------------------------------------------------------------------------

def _register_popup_capture_handlers() -> None:
    for _namespace, _settings_prefix in (
        ("shortener", "shortener"), ("sysaccess", "shortener.system_access"), ("rtsave", "rt_save"),
    ):
        for _popup_kind, (_field_name, _default_text, _title) in _POPUP_KINDS.items():
            text_session_kind = f"{_namespace}_set_{_popup_kind}_text"
            photo_session_kind = f"{_namespace}_set_{_popup_kind}_photo"

            async def _capture_popup_text(client, message: Message, field_name=_field_name,
                                           prefix=_settings_prefix) -> None:
                session_manager.clear(message.from_user.id)
                await db.update_settings({f"{prefix}.{field_name}.text": message.text.html})
                await message.reply_text("✅ Popup text updated.")

            async def _capture_popup_photo(client, message: Message, field_name=_field_name,
                                            prefix=_settings_prefix) -> None:
                session_manager.clear(message.from_user.id)
                patch = {f"{prefix}.{field_name}.photo_file_id": message.photo.file_id}
                if message.caption:
                    patch[f"{prefix}.{field_name}.text"] = message.caption.html
                await db.update_settings(patch)
                await message.reply_text("✅ Popup photo updated.")

            app.on_message(
                filters.private & session_is(text_session_kind) & filters.text & ~filters.command(_ADMIN_COMMANDS)
            )(_capture_popup_text)
            app.on_message(
                filters.private & session_is(photo_session_kind) & filters.photo
            )(_capture_popup_photo)


_register_popup_capture_handlers()

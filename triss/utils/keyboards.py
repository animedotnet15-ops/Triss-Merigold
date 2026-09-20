"""
triss.utils.keyboards
======================
Every inline keyboard used by the bot lives here, built with Kurigram's
ButtonStyle system (PRIMARY / SUCCESS / DANGER) as required by spec.

Callback data convention: "namespace:action[:arg]", always short (Telegram
caps callback_data at 64 bytes) and never containing secrets (no storage
channel IDs, no Mongo ObjectIds — only opaque tokens/kinds that are looked
up server-side and re-validated against OWNER_ID / the database).
"""

from __future__ import annotations

from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ButtonStyle


def btn(text: str, callback_data: str, style: ButtonStyle = ButtonStyle.PRIMARY) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data, style=style)


def url_btn(text: str, url: str, style: ButtonStyle = ButtonStyle.SUCCESS) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, url=url, style=style)


# ---------------------------------------------------------------------------
# Settings — main menu
# ---------------------------------------------------------------------------

def settings_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("🏠 ᴡᴇʟᴄᴏᴍᴇ", "settings:welcome"), btn("🌐 ᴘʀɪᴠᴀᴛᴇ ʟɪɴᴋs", "settings:links")],
        [btn("📣 ғᴏʀᴄᴇ sᴜʙ", "settings:forcesub"), btn("🧹 ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ", "settings:autodelete")],
        [btn("🌐 sʜᴏʀᴛᴇɴᴇʀ", "settings:shortener")],
        [btn("⚙️ ʙᴏᴛ ᴍᴀɪɴᴛᴇɴᴀɴᴄᴇ", "settings:maintenance")],
        [btn("🗄️ ʙᴀᴄᴋᴜᴘ & ʀᴇsᴛᴏʀᴇ", "settings:backup")],
        [btn("🏪 sᴛᴏʀᴇ ᴄʜᴀɴɴᴇʟ", "settings:storechannel")],
    ])


def back_btn(target: str = "settings:main") -> InlineKeyboardButton:
    return btn("⬅️ Back", target)


# ---------------------------------------------------------------------------
# Welcome submenu
# ---------------------------------------------------------------------------

def welcome_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("📸 Set Photo", "welcome:setphoto"), btn("💬 Set Welcome", "welcome:settext")],
        [btn("🙈 Set Spoiler Image", "welcome:spoiler"), btn("🎀 Set Sticker", "welcome:sticker")],
        [
            btn("🐌 Slow", "welcome:speed:slow", ButtonStyle.PRIMARY),
            btn("🌿 Default", "welcome:speed:default", ButtonStyle.SUCCESS),
            btn("🔥 Speed", "welcome:speed:speed", ButtonStyle.DANGER),
        ],
        [btn("👀 Preview", "welcome:preview")],
        [back_btn()],
    ])


def sticker_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("🎀 Set Sticker", "sticker:set"), btn("🗑️ Remove Sticker", "sticker:remove", ButtonStyle.DANGER)],
        [btn("✅ Enable", "sticker:enable", ButtonStyle.SUCCESS), btn("🚫 Disable", "sticker:disable", ButtonStyle.DANGER)],
        [btn("👀 Preview", "sticker:preview")],
        [back_btn("settings:welcome")],
    ])


def animation_speed_choice() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        btn("🐌 Slow", "start_speed:slow", ButtonStyle.PRIMARY),
        btn("🌿 Default", "start_speed:default", ButtonStyle.SUCCESS),
        btn("🔥 Speed", "start_speed:speed", ButtonStyle.DANGER),
    ]])


# ---------------------------------------------------------------------------
# Force Sub submenu
# ---------------------------------------------------------------------------

def forcesub_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("📣 Add Channel", "forcesub:addchannel"), btn("👥 Add Group", "forcesub:addgroup")],
        [btn("🗂️ Add Folder", "forcesub:addfolder")],
        [btn("📋 List", "forcesub:list"), btn("❌ Remove", "forcesub:remove", ButtonStyle.DANGER)],
        [btn("🧹 Clear", "forcesub:clear", ButtonStyle.DANGER)],
        [back_btn()],
    ])


def forcesub_join_mode_menu(target: str) -> InlineKeyboardMarkup:
    """`target` is "channel" or "group" - shown right after 'Add Channel'/
    'Add Group' so the owner picks how users join BEFORE forwarding the
    chat, since the invite link itself differs (see forcesub:addX:MODE
    handlers in callbacks.py, which pass this through to
    triss.database.models.add_force_sub's join_mode)."""
    return InlineKeyboardMarkup([
        [btn("🔗 Normal Join", f"forcesub:add{target}:normal", ButtonStyle.SUCCESS)],
        [btn("📝 Join Request", f"forcesub:add{target}:request", ButtonStyle.PRIMARY)],
        [back_btn("settings:forcesub")],
    ])


def force_sub_user_keyboard(entries: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for entry in entries:
        link = entry.get("invite_link") or ""
        title = entry.get("title") or entry.get("kind", "").title()
        label = "📝 Request to Join" if entry.get("join_mode") == "request" else "📢 Join"
        if link:
            rows.append([url_btn(f"{label} {title}", link, ButtonStyle.SUCCESS)])
    rows.append([btn("✅ Verify", "forcesub:verify", ButtonStyle.PRIMARY)])
    return InlineKeyboardMarkup(rows)


def remove_forcesub_list(entries: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for i, entry in enumerate(entries):
        mode_tag = " 📝" if entry.get("join_mode") == "request" else ""
        label = f"❌ {entry.get('title', entry.get('kind'))}{mode_tag}"
        rows.append([btn(label, f"forcesub:rm:{entry['kind']}:{entry.get('chat_id')}", ButtonStyle.DANGER)])
    rows.append([back_btn("settings:forcesub")])
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# Auto Delete submenu
# ---------------------------------------------------------------------------

def autodelete_menu(notify: bool = True) -> InlineKeyboardMarkup:
    notify_label = "🔔 Notify: 🟢 ON" if notify else "🔕 Notify: 🔴 OFF"
    notify_style = ButtonStyle.SUCCESS if notify else ButtonStyle.DANGER
    return InlineKeyboardMarkup([
        [
            btn("10s", "autodelete:set:10", ButtonStyle.PRIMARY),
            btn("1m", "autodelete:set:60", ButtonStyle.PRIMARY),
            btn("1h", "autodelete:set:3600", ButtonStyle.PRIMARY),
        ],
        [btn("✏️ Custom", "autodelete:custom")],
        [btn("✅ Enable", "autodelete:enable", ButtonStyle.SUCCESS), btn("🚫 Disable", "autodelete:disable", ButtonStyle.DANGER)],
        [btn(notify_label, "autodelete:togglenotify", notify_style)],
        [back_btn()],
    ])


# ---------------------------------------------------------------------------
# Maintenance submenu
# ---------------------------------------------------------------------------

def maintenance_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("🤸 Active", "maintenance:off", ButtonStyle.SUCCESS), btn("🧑‍🔧 Maintenance", "maintenance:on", ButtonStyle.DANGER)],
        [back_btn()],
    ])


# ---------------------------------------------------------------------------
# Backup & Restore submenu
# ---------------------------------------------------------------------------

def backup_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("💾 Create Backup", "backup:create", ButtonStyle.SUCCESS)],
        [btn("♻️ Restore Backup", "backup:restore", ButtonStyle.PRIMARY)],
        [btn("📋 Backup Info", "backup:info")],
        [btn("🗑️ Delete Backup", "backup:delete", ButtonStyle.DANGER)],
        [back_btn()],
    ])


def confirm_restore_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("✅ Confirm Restore", "backup:restore:confirm", ButtonStyle.DANGER),
         btn("❌ Cancel", "backup:restore:cancel", ButtonStyle.PRIMARY)],
    ])


# ---------------------------------------------------------------------------
# Store Channel submenu
# ---------------------------------------------------------------------------

def storechannel_menu(configured: bool) -> InlineKeyboardMarkup:
    rows = [[btn("🔧 Set Store Channel", "storechannel:set", ButtonStyle.PRIMARY)]]
    if configured:
        rows.append([btn("🗑️ Unset", "storechannel:unset", ButtonStyle.DANGER)])
    rows.append([back_btn()])
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# Shortener submenu
# ---------------------------------------------------------------------------

def shortener_menu(enabled: bool) -> InlineKeyboardMarkup:
    toggle_label = "🔄 Shortener: 🟢 ON" if enabled else "🔄 Shortener: 🔴 OFF"
    toggle_style = ButtonStyle.SUCCESS if enabled else ButtonStyle.DANGER
    return InlineKeyboardMarkup([
        [btn("🌍 Set Shortener Domain", "shortener:setdomain")],
        [btn("🔒 Set Shortener API", "shortener:setapi")],
        [btn("🕒 Set Minimum Time", "shortener:setmin"), btn("⏰ Set Maximum Time", "shortener:setmax")],
        [btn("💬 Verify Popup", "shortener:verifymsg"), btn("🚨 Bypass Popup", "shortener:bypassmsg")],
        [btn("🎯 Anti-Bypass", "shortener:antibypass")],
        [btn("▶️ Tutorial Video", "shortener:tutorial")],
        [btn("🧪 Test", "shortener:test")],
        [btn(toggle_label, "shortener:toggle", toggle_style)],
        [back_btn()],
    ])


def shortener_message_menu(kind: str, has_photo: bool, spoiler: bool) -> InlineKeyboardMarkup:
    """`kind` is "verifymsg", "bypassmsg" or "mutedmsg" - shared layout for
    all three customizable popups (feature: text + optional photo with a
    spoiler toggle)."""
    spoiler_label = "🙈 Spoiler: 🟢 ON" if spoiler else "🙈 Spoiler: 🔴 OFF"
    spoiler_style = ButtonStyle.SUCCESS if spoiler else ButtonStyle.DANGER
    rows = [
        [btn("💬 Set Text", f"shortener:{kind}:settext")],
        [btn("📸 Set Photo", f"shortener:{kind}:setphoto")],
    ]
    if has_photo:
        rows.append([btn("🗑️ Remove Photo", f"shortener:{kind}:removephoto", ButtonStyle.DANGER)])
    rows.append([btn(spoiler_label, f"shortener:{kind}:spoiler", spoiler_style)])
    rows.append([btn("↩️ Reset to Default", f"shortener:{kind}:reset", ButtonStyle.DANGER)])
    rows.append([btn("👀 Preview", f"shortener:{kind}:preview")])
    rows.append([back_btn("settings:shortener")])
    return InlineKeyboardMarkup(rows)


def antibypass_menu(strike_limit: int, mute_seconds: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn(f"🎯 Strike Limit: {strike_limit}", "shortener:antibypass:setstrikes")],
        [btn(f"🔇 Mute Duration: {mute_seconds}s", "shortener:antibypass:setmute")],
        [btn("🚨 Bypass Popup", "shortener:bypassmsg"), btn("🔇 Muted Popup", "shortener:mutedmsg")],
        [back_btn("settings:shortener")],
    ])


def shortener_tutorial_menu(configured: bool) -> InlineKeyboardMarkup:
    rows = [[btn("✏️ Set / Replace", "shortener:tutorial:set", ButtonStyle.PRIMARY)]]
    if configured:
        rows.append([btn("🗑️ Remove", "shortener:tutorial:remove", ButtonStyle.DANGER)])
    rows.append([back_btn("settings:shortener")])
    return InlineKeyboardMarkup(rows)


def shortener_verification_keyboard(short_url: str, tutorial_url: str | None) -> InlineKeyboardMarkup:
    rows = [[url_btn("🌀 ᴠᴇʀɪғʏ & ɢᴇᴛ ғɪʟᴇ", short_url, ButtonStyle.PRIMARY)]]
    if tutorial_url:
        rows.append([url_btn("👀 ᴛᴜᴛᴏʀɪᴀʟ ᴠɪᴅᴇᴏ", tutorial_url, ButtonStyle.PRIMARY)])
    return InlineKeyboardMarkup(rows)


def shortener_retry_keyboard(access_token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[btn("🔄 ᴛʀʏ ᴀɢᴀɪɴ", f"shortener:retry:{access_token}", ButtonStyle.PRIMARY)]])


# ---------------------------------------------------------------------------
# genlink / batch — generated link response
# ---------------------------------------------------------------------------

def generated_link_keyboard(link: str) -> InlineKeyboardMarkup:
    """Attached to the /genlink and /batch "link generated"
    response so the owner has a tappable open/share button in addition to
    the raw link text. This is the Telegram deep link itself (`t.me/<bot>?
    start=<token>`) — Shortener, per spec, only ever wraps a link at
    *access* time (see `triss.services.shortener` / README "Shortener
    verification flow"), never at generation time, so this button
    intentionally does not go through the shortener."""
    return InlineKeyboardMarkup([[url_btn("🔗 ᴏᴘᴇɴ ʟɪɴᴋ", link, ButtonStyle.SUCCESS)]])


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

def cancel_only(callback: str = "generic:cancel") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[btn("❌ Cancel", callback, ButtonStyle.DANGER)]])


def try_again_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[btn("🔁 Try Again", "forcesub:verify", ButtonStyle.PRIMARY)]])
    

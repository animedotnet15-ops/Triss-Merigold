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

def settings_main_menu(settings: dict | None = None) -> InlineKeyboardMarkup:
    """`settings` is optional only so old call sites that haven't been
    updated don't crash outright - every real call site now passes it so
    each toggle-style button shows its current ON/OFF state at a glance,
    instead of the owner having to open each submenu to check."""
    s = settings or {}
    fs = " ON" if s.get("force_sub_enabled", True) else " OFF"
    ad = " ON" if s.get("auto_delete", {}).get("enabled") else " OFF"
    sh = " ON" if s.get("shortener", {}).get("enabled") else " OFF"
    sa = " ON" if s.get("shortener", {}).get("system_access", {}).get("enabled") else " OFF"
    ld = " ON" if s.get("linkdl", {}).get("enabled") else " OFF"
    rc = " ON" if s.get("restrict_content") else " OFF"
    mt = " ON" if s.get("maintenance") else " OFF"
    return InlineKeyboardMarkup([
        [btn("🏠 ᴡᴇʟᴄᴏᴍᴇ", "settings:welcome"), btn("💬 ᴄᴏᴍᴍᴇɴᴛs", "settings:links")],
        [btn(f"📣 ғᴏʀᴄᴇ sᴜʙ{fs}", "settings:forcesub"), btn(f"🧹 ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ{ad}", "settings:autodelete")],
        [btn(f"🌐 sʜᴏʀᴛᴇɴᴇʀ{sh}", "settings:shortener"), btn(f"♻️ sʏsᴛᴇᴍ ᴀᴄᴄᴇss{sa}", "settings:systemaccess")],
        [btn(f"📥 ᴅɪʀᴇᴄᴛ ᴅᴏᴡɴʟᴏᴀᴅ{ld}", "settings:linkdl"), btn(f"🔒 ʀᴇsᴛʀɪᴄᴛ ᴄᴏɴᴛᴇɴᴛ{rc}", "restrict:toggle")],
        [btn(f"⚙️ ʙᴏᴛ ᴍᴀɪɴᴛᴇɴᴀɴᴄᴇ{mt}", "settings:maintenance")],
        [btn("🗄️ ʙᴀᴄᴋᴜᴘ & ʀᴇsᴛᴏʀᴇ", "settings:backup")],
        [btn("🏪 sᴛᴏʀᴇ ᴄʜᴀɴɴᴇʟ", "settings:storechannel"), btn("🧾 ʟᴏɢ ᴄʜᴀɴɴᴇʟ", "settings:logchannel")],
        [btn("💎 Premium", "settings:premium"), btn("👥 Group Settings", "settings:groupsettings")],
        [btn("🔓 Restrict Save", "settings:rtsave")],
    ])


_PLAN_EMOJI = {"bronze": "🥉", "silver": "🥈", "gold": "🥇"}


def premium_menu(premium: dict) -> InlineKeyboardMarkup:
    scanner_set = "✅" if premium.get("scanner_file_id") else "❌"
    upi_set = "✅" if premium.get("upi_id") else "❌"
    rows = [
        [btn(f"🖼️ Set Scanner Image {scanner_set}", "premset:scanner")],
        [btn(f"💳 Set UPI ID {upi_set}", "premset:upi")],
        [btn("✏️ Customise Buy Message", "premset:custommsg")],
    ]
    for key, plan in premium.get("plans", {}).items():
        emoji = _PLAN_EMOJI.get(key, "•")
        rows.append([btn(f"{emoji} {plan.get('label', key.title())} — ₹{plan.get('price')} ({plan.get('days')}d)",
                          f"premset:editplan:{key}")])
    rows.append([back_btn()])
    return InlineKeyboardMarkup(rows)


def buy_premium_menu(plans: dict) -> InlineKeyboardMarkup:
    rows = []
    for key, plan in plans.items():
        emoji = _PLAN_EMOJI.get(key, "•")
        rows.append([btn(f"{emoji} {plan.get('label', key.title())} — ₹{plan.get('price')} ({plan.get('days')} Days)",
                          f"buyprem:plan:{key}")])
    return InlineKeyboardMarkup(rows)


def premium_plan_menu() -> InlineKeyboardMarkup:
    return cancel_only("buyprem:cancel")


def premium_approval_menu(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        btn("✅ Approve", f"prem:approve:{request_id}", ButtonStyle.SUCCESS),
        btn("❌ Reject", f"prem:reject:{request_id}", ButtonStyle.DANGER),
    ]])


def group_settings_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("🔗 Connect", "groupset:connect"), btn("🔍 Filter", "groupset:filter")],
        [back_btn()],
    ])


def group_connect_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("➕ Add", "groupconn:add", ButtonStyle.PRIMARY), btn("📋 List", "groupconn:list")],
        [back_btn("settings:groupsettings")],
    ])


def group_connect_list_menu(groups: list[dict]) -> InlineKeyboardMarkup:
    rows = [[btn(f"🗑️ {g.get('title') or g['chat_id']}", f"groupconn:rm:{g['chat_id']}", ButtonStyle.DANGER)]
            for g in groups]
    rows.append([back_btn("settings:groupsettings")])
    return InlineKeyboardMarkup(rows)


def filter_settings_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("📋 List", "filtermgmt:list"), btn("🗑️ Remove", "filtermgmt:removelist")],
        [back_btn("settings:groupsettings")],
    ])


def filter_remove_menu(filters_: list[dict]) -> InlineKeyboardMarkup:
    rows = [[btn(f"🗑️ {f['display']}", f"filtermgmt:rm:{f['keyword']}", ButtonStyle.DANGER)]
            for f in filters_]
    rows.append([back_btn("settings:groupsettings")])
    return InlineKeyboardMarkup(rows)


def linkdl_menu(linkdl: dict, has_public_base_url: bool) -> InlineKeyboardMarkup:
    enabled = bool(linkdl.get("enabled"))
    toggle_label = "🌍 Public Use: ON" if enabled else "🌍 Public Use: OFF"
    toggle_style = ButtonStyle.SUCCESS if enabled else ButtonStyle.DANGER
    media_type = linkdl.get("media_type", "none")
    spoiler = bool(linkdl.get("spoiler"))
    rows = [
        [btn("💬 Set Caption", "linkdl:settext")],
        [btn("↩️ Reset Caption", "linkdl:reset", ButtonStyle.DANGER)],
        [btn(f"🖼️ Photo{' ✅' if media_type == 'photo' else ''}", "linkdl:setphoto"),
         btn(f"🎥 Video{' ✅' if media_type == 'video' else ''}", "linkdl:setvideo")],
        [btn(f"🎞️ GIF{' ✅' if media_type == 'animation' else ''}", "linkdl:setgif"),
         btn("🗑️ Remove Media", "linkdl:removemedia")],
        [btn(f"🙈 Spoiler: {'ON' if spoiler else 'OFF'}", "linkdl:spoiler")],
        [btn(toggle_label, "linkdl:toggle", toggle_style)],
    ]
    if not has_public_base_url:
        rows.insert(0, [btn("⚠️ PUBLIC_BASE_URL not set — see /linkdl", "linkdl:noop", ButtonStyle.DANGER)])
    rows.append([back_btn()])
    return InlineKeyboardMarkup(rows)


def back_btn(target: str = "settings:main") -> InlineKeyboardButton:
    return btn("⬅️ Back", target)


# ---------------------------------------------------------------------------
# Welcome submenu
# ---------------------------------------------------------------------------

def welcome_menu(media_type: str = "photo") -> InlineKeyboardMarkup:
    def tag(kind: str) -> str:
        return " ✅" if media_type == kind else ""
    return InlineKeyboardMarkup([
        [
            btn(f"📸 Photo{tag('photo')}", "welcome:setphoto"),
            btn(f"🎥 Video{tag('video')}", "welcome:setvideo"),
            btn(f"🎞️ GIF{tag('animation')}", "welcome:setgif"),
        ],
        [btn("💬 Set Welcome", "welcome:settext")],
        [btn("🙈 Set Spoiler Media", "welcome:spoiler"), btn("🎀 Set Sticker", "welcome:sticker")],
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
        [btn("💬 Custom Message", "forcesub:message"), btn("✏️ Button Names", "forcesub:editbtn")],
        [btn("🧹 Clear", "forcesub:clear", ButtonStyle.DANGER)],
        [back_btn()],
    ])


def forcesub_message_menu(has_photo: bool, spoiler: bool) -> InlineKeyboardMarkup:
    """Item 2: customizable Force Sub prompt text/photo/spoiler - same
    layout convention as shortener_message_menu (text/photo/spoiler/reset/
    preview), kept as its own function since Force Sub has only ONE
    message (not three like the shortener popups)."""
    spoiler_label = "🙈 Spoiler: ON" if spoiler else "🙈 Spoiler: OFF"
    spoiler_style = ButtonStyle.SUCCESS if spoiler else ButtonStyle.DANGER
    rows = [
        [btn("💬 Set Text", "forcesub:message:settext")],
        [btn("📸 Set Photo", "forcesub:message:setphoto")],
    ]
    if has_photo:
        rows.append([btn("🗑️ Remove Photo", "forcesub:message:removephoto", ButtonStyle.DANGER)])
    rows.append([btn(spoiler_label, "forcesub:message:spoiler", spoiler_style)])
    rows.append([btn("↩️ Reset to Default", "forcesub:message:reset", ButtonStyle.DANGER)])
    rows.append([btn("👀 Preview", "forcesub:message:preview")])
    rows.append([back_btn("settings:forcesub")])
    return InlineKeyboardMarkup(rows)


def forcesub_editbtn_list(entries: list[dict]) -> InlineKeyboardMarkup:
    """Item 2: tap an entry to set/change its custom Join button label."""
    rows = []
    for entry in entries:
        current = entry.get("button_text") or entry.get("title") or entry.get("kind")
        rows.append([btn(f"✏️ {current}", f"forcesub:editbtn:{entry['kind']}:{entry.get('chat_id')}")])
    rows.append([back_btn("settings:forcesub")])
    return InlineKeyboardMarkup(rows)


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
        custom_label = entry.get("button_text")
        if custom_label:
            label = custom_label
        else:
            prefix = "📝 Request to Join" if entry.get("join_mode") == "request" else "📢 Join"
            label = f"{prefix} {title}"
        if link:
            rows.append([url_btn(label, link, ButtonStyle.SUCCESS)])
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
    notify_label = "🔔 Notify: ON" if notify else "🔕 Notify: OFF"
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

def channel_menu(kind: str) -> InlineKeyboardMarkup:
    """Shared by Store Channel ("storage") and Log Channel ("log")."""
    return InlineKeyboardMarkup([
        [btn("➕ Add", f"chan:{kind}:add", ButtonStyle.PRIMARY), btn("📋 List", f"chan:{kind}:list")],
        [back_btn()],
    ])


def channel_list_menu(kind: str, channels: list[dict], active_id) -> InlineKeyboardMarkup:
    """Tap a channel to make it the active one; 🗑️ removes it from the list."""
    rows = []
    for ch in channels:
        mark = "✅ " if ch["chat_id"] == active_id else ""
        rows.append([
            btn(f"{mark}{ch.get('title') or ch['chat_id']}", f"chan:{kind}:use:{ch['chat_id']}"),
            btn("🗑️", f"chan:{kind}:rm:{ch['chat_id']}", ButtonStyle.DANGER),
        ])
    rows.append([back_btn("settings:storechannel" if kind == "storage" else "settings:logchannel")])
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# Shortener submenu
# ---------------------------------------------------------------------------

def shortener_menu(enabled: bool) -> InlineKeyboardMarkup:
    toggle_label = "🔄 Shortener: ON" if enabled else "🔄 Shortener: OFF"
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


def system_access_menu(sa: dict) -> InlineKeyboardMarkup:
    """Mirrors shortener_menu's layout exactly, but every callback lives
    under the 'sysaccess:' namespace instead of 'shortener:' — System
    Access has its own fully separate Domain/API/Min-Max/Popups/
    Anti-Bypass, independent from Old Method's (see DEFAULT_SETTINGS in
    triss.database.mongodb). Adds the one setting Old Method doesn't
    have: how long a single successful verification grants unlimited
    bot access for."""
    enabled = sa.get("enabled", False)
    toggle_label = "♻️ System Access: ON" if enabled else "♻️ System Access: OFF"
    toggle_style = ButtonStyle.SUCCESS if enabled else ButtonStyle.DANGER
    duration = sa.get("access_duration_seconds", 21600)
    return InlineKeyboardMarkup([
        [btn("🌍 Set Domain", "sysaccess:setdomain")],
        [btn("🔒 Set API Key", "sysaccess:setapi")],
        [btn("🕒 Set Minimum Time", "sysaccess:setmin"), btn("⏰ Set Maximum Time", "sysaccess:setmax")],
        [btn("💬 Verify Popup", "sysaccess:verifymsg"), btn("🚨 Bypass Popup", "sysaccess:bypassmsg")],
        [btn("🎯 Anti-Bypass", "sysaccess:antibypass")],
        [btn("▶️ Tutorial Video", "sysaccess:tutorial")],
        [btn(f"⏱️ Verify Time: {format_duration(duration)}", "sysaccess:duration")],
        [btn("🧪 Test", "sysaccess:test")],
        [btn(toggle_label, "sysaccess:toggle", toggle_style)],
        [back_btn()],
    ])


def rt_save_menu(rt_save: dict) -> InlineKeyboardMarkup:
    """Mirrors system_access_menu's layout exactly - rt_save has its own
    fully separate Domain/API/Min-Max/Popups/Anti-Bypass (see
    DEFAULT_SETTINGS in triss.database.mongodb), plus two toggles System
    Access doesn't need: Shortener (whether verification is required at
    all) and Public Use (whether /rt_save works at all)."""
    enabled = bool(rt_save.get("enabled"))
    public_label = "🌍 Public Use: ON" if enabled else "🌍 Public Use: OFF"
    public_style = ButtonStyle.SUCCESS if enabled else ButtonStyle.DANGER
    shortener_on = rt_save.get("shortener_enabled", True)
    shortener_label = "🔄 Shortener: ON" if shortener_on else "🔄 Shortener: OFF"
    shortener_style = ButtonStyle.SUCCESS if shortener_on else ButtonStyle.DANGER
    duration = rt_save.get("verify_duration_seconds", 3600)
    return InlineKeyboardMarkup([
        [btn("🌍 Set Domain", "rtsave:setdomain")],
        [btn("🔒 Set API Key", "rtsave:setapi")],
        [btn("🕒 Set Minimum Time", "rtsave:setmin"), btn("⏰ Set Maximum Time", "rtsave:setmax")],
        [btn("💬 Verify Popup", "rtsave:verifymsg"), btn("🚨 Bypass Popup", "rtsave:bypassmsg")],
        [btn("🎯 Anti-Bypass", "rtsave:antibypass")],
        [btn("▶️ Tutorial Video", "rtsave:tutorial")],
        [btn(f"⏱️ Verify Time: {format_duration(duration)}", "rtsave:duration")],
        [btn("🧪 Test", "rtsave:test")],
        [btn(shortener_label, "rtsave:toggleshortener", shortener_style)],
        [btn(public_label, "rtsave:toggle", public_style)],
        [back_btn()],
    ])


def rt_save_duration_menu(current_seconds: int) -> InlineKeyboardMarkup:
    presets = [
        (1800, "30m"), (3600, "1h"), (10800, "3h"),
        (21600, "6h"), (43200, "12h"), (86400, "24h"),
    ]
    rows = []
    row = []
    for seconds, label in presets:
        marker = " ✅" if seconds == current_seconds else ""
        row.append(btn(f"{label}{marker}", f"rtsave:setduration:{seconds}",
                        ButtonStyle.SUCCESS if seconds == current_seconds else ButtonStyle.PRIMARY))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([back_btn("settings:rtsave")])
    return InlineKeyboardMarkup(rows)


def format_duration(seconds: int) -> str:
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def system_access_duration_menu(current_seconds: int) -> InlineKeyboardMarkup:
    """Fixed presets only, per spec — no custom text-input option."""
    presets = [
        (1800, "30m"), (3600, "1h"), (10800, "3h"),
        (21600, "6h"), (43200, "12h"), (86400, "24h"),
    ]
    rows = []
    row = []
    for seconds, label in presets:
        marker = " ✅" if seconds == current_seconds else ""
        row.append(btn(f"{label}{marker}", f"sysaccess:setduration:{seconds}",
                        ButtonStyle.SUCCESS if seconds == current_seconds else ButtonStyle.PRIMARY))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([back_btn("settings:systemaccess")])
    return InlineKeyboardMarkup(rows)


_NAMESPACE_BACK_TARGETS = {"shortener": "settings:shortener", "sysaccess": "settings:systemaccess", "rtsave": "settings:rtsave"}


def _settings_back_target(namespace: str) -> str:
    return _NAMESPACE_BACK_TARGETS.get(namespace, "settings:shortener")


def shortener_message_menu(kind: str, has_photo: bool, spoiler: bool, namespace: str = "shortener") -> InlineKeyboardMarkup:
    """`kind` is "verifymsg", "bypassmsg" or "mutedmsg" - shared layout for
    all three customizable popups (feature: text + optional photo with a
    spoiler toggle). `namespace` is "shortener" (Old Method) or
    "sysaccess" (System Access) - both modes reuse this exact same menu
    layout and popup-editing flow, just routed to their own independent
    settings sub-document."""
    spoiler_label = "🙈 Spoiler: ON" if spoiler else "🙈 Spoiler: OFF"
    spoiler_style = ButtonStyle.SUCCESS if spoiler else ButtonStyle.DANGER
    rows = [
        [btn("💬 Set Text", f"{namespace}:{kind}:settext")],
        [btn("📸 Set Photo", f"{namespace}:{kind}:setphoto")],
    ]
    if has_photo:
        rows.append([btn("🗑️ Remove Photo", f"{namespace}:{kind}:removephoto", ButtonStyle.DANGER)])
    rows.append([btn(spoiler_label, f"{namespace}:{kind}:spoiler", spoiler_style)])
    rows.append([btn("↩️ Reset to Default", f"{namespace}:{kind}:reset", ButtonStyle.DANGER)])
    rows.append([btn("👀 Preview", f"{namespace}:{kind}:preview")])
    rows.append([back_btn(_settings_back_target(namespace))])
    return InlineKeyboardMarkup(rows)


def antibypass_menu(strike_limit: int, mute_seconds: int, namespace: str = "shortener") -> InlineKeyboardMarkup:
    back_target = _settings_back_target(namespace)
    return InlineKeyboardMarkup([
        [btn(f"🎯 Strike Limit: {strike_limit}", f"{namespace}:antibypass:setstrikes")],
        [btn(f"🔇 Mute Duration: {mute_seconds}s", f"{namespace}:antibypass:setmute")],
        [btn("🚨 Bypass Popup", f"{namespace}:bypassmsg"), btn("🔇 Muted Popup", f"{namespace}:mutedmsg")],
        [back_btn(back_target)],
    ])


def shortener_tutorial_menu(configured: bool, namespace: str = "shortener") -> InlineKeyboardMarkup:
    back_target = _settings_back_target(namespace)
    rows = [[btn("✏️ Set / Replace", f"{namespace}:tutorial:set", ButtonStyle.PRIMARY)]]
    if configured:
        rows.append([btn("🗑️ Remove", f"{namespace}:tutorial:remove", ButtonStyle.DANGER)])
    rows.append([back_btn(back_target)])
    return InlineKeyboardMarkup(rows)


def shortener_verification_keyboard(short_url: str, tutorial_url: str | None) -> InlineKeyboardMarkup:
    rows = [[url_btn("🌀 ᴠᴇʀɪғʏ & ɢᴇᴛ ғɪʟᴇ", short_url, ButtonStyle.PRIMARY)]]
    if tutorial_url:
        rows.append([url_btn("👀 ᴛᴜᴛᴏʀɪᴀʟ ᴠɪᴅᴇᴏ", tutorial_url, ButtonStyle.PRIMARY)])
    return InlineKeyboardMarkup(rows)


def shortener_retry_keyboard(access_token: str, mode: str = "per_link") -> InlineKeyboardMarkup:
    """`mode` is embedded (as a short code to stay well under Telegram's
    64-byte callback_data limit) so retrying a System Access verification
    re-launches under System Access's own settings, never Old Method's."""
    mode_code = "sa" if mode == "system_access" else ("rt" if mode == "rt_save" else "pl")
    return InlineKeyboardMarkup([[
        btn("🔄 ᴛʀʏ ᴀɢᴀɪɴ", f"shortener:retry:{mode_code}:{access_token}", ButtonStyle.PRIMARY)
    ]])


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

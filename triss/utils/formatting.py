"""
triss.utils.formatting
=======================
Default text templates (small-caps aesthetic, per spec), rendered as
Telegram HTML formatting entities (the Client's pinned parse_mode - see
triss/bot.py's Client(parse_mode=ParseMode.HTML, ...)), and the
{mention}/{first}/{last}/{username}/{id} variable substitution used in
the welcome message.

This module is the single centralized place that decides how the bot's
static small-caps UI text is turned into real Telegram formatting entities
and how dynamic values get safely substituted into templates. Nothing
outside this module should hand-roll markup - handlers just import the
rendered constants / call render_welcome().

--------------------------------------------------------------------------
WHY HTML, NOT MARKDOWN (bug fix — "some fonts/symbols don't work")
--------------------------------------------------------------------------
This bot used to rely on Pyrogram's Markdown dialect (`**bold**`, etc.),
which has NINE reserved characters: \\ * _ ~ ` | [ ] ( ). Any owner-typed
custom text (a welcome message, a popup, a Force Sub message) that
happened to contain one of those characters LITERALLY — a pasted "fancy
font"/decorative-symbol string, a stray asterisk, a plain underscore —
could be misread as a formatting delimiter, corrupting the message or
truncating it entirely. This is what "font/symbol not supported" bugs
like this almost always turn out to be.

HTML has only THREE reserved characters: & < >. `_escape_html_value()`
below escapes exactly those (and only those) in dynamic values, which
is far less likely to misfire on arbitrary user text. `_bold()` now
wraps templates in real `<b>...</b>` tags, and every place that used to
hand-roll a Markdown-style `` `code` `` span (masked API keys, links,
user IDs — see triss/handlers/*.py) now uses `<code>...</code>` instead.
triss/bot.py pins the Client's parse_mode to HTML so this is consistent
bot-wide — no module needs to pass parse_mode explicitly.

`SHORTENER_BYPASS_TEXT` contains a literal "**" (part of the censored
word "f**ker") — under HTML this needs NO escaping/workaround at all,
since asterisks aren't special in HTML. (The Markdown version of this
file used to backslash-escape it; that workaround no longer exists,
and is not missed.)

--------------------------------------------------------------------------
INJECTION / CORRUPTION FIX
--------------------------------------------------------------------------
`render_welcome()` substitutes user-controlled values (first name, last
name, username) into a template. Telegram display names can legally
contain HTML-significant characters (& < >) — substituting them unescaped
could both corrupt the surrounding template's formatting and let a user
inject their own markup into the bot's own message. `_escape_html_value()`
escapes only those dynamic values, so the static template's own
intentional markup is never touched (no double-escaping) while dynamic
content can never break or hijack it.
"""

from __future__ import annotations

from typing import Optional

# The only three characters HTML parse mode treats specially. Order
# matters: & must be escaped first, or the &amp;/&lt;/&gt; we insert for
# the other two would themselves get re-escaped on a later pass.
_HTML_SPECIAL_CHARS = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))


def _escape_html_value(value: Optional[str]) -> str:
    """Escapes HTML-significant characters in a single dynamic value
    (never a whole template) before it is substituted into rendered text."""
    if not value:
        return ""
    for ch, escaped in _HTML_SPECIAL_CHARS:
        value = value.replace(ch, escaped)
    return value


def _bold(text: str) -> str:
    """Wraps one of this module's own static, fully-controlled template
    strings in Telegram native HTML bold. Never apply this to dynamic
    or user-supplied text - use `_escape_html_value()` for that."""
    return f"<b>{text}</b>"


DEFAULT_WELCOME_TEXT = _bold(
    "╭━━━〔 🦋 ʜᴇʟʟᴏ, {mention} 〕━━━╮\n"
    "\n"
    "💜 ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ ᴛʀɪss 🌸\n"
    "\n"
    "🌷 ʏᴏᴜ'ᴠᴇ ᴊᴜsᴛ ᴇɴᴛᴇʀᴇᴅ ʏᴏᴜʀ ʟɪᴛᴛʟᴇ ғɪʟᴇ ᴜɴɪᴠᴇʀsᴇ. ☁️\n"
    "\n"
    "📨 ʏᴏᴜ ʙʀɪɴɢ ᴛʜᴇ ғɪʟᴇ...\n"
    "🧚 ᴛʀɪss ᴛᴜʀɴs ɪᴛ ɪɴᴛᴏ sᴏᴍᴇᴛʜɪɴɢ sʜᴀʀᴀʙʟᴇ.\n"
    "\n"
    "╭───────────────╮\n"
    "\n"
    "🎀 ᴅʀᴏᴘ ɪᴛ ʜᴇʀᴇ\n"
    "🌐 ɢᴇᴛ ʏᴏᴜʀ ʟɪɴᴋ\n"
    "🪄 sʜᴀʀᴇ ɪᴛ ᴀɴʏᴡʜᴇʀᴇ\n"
    "\n"
    "╰───────────────╯\n"
    "\n"
    "🍃 ɴᴏ ᴄᴏᴍᴘʟɪᴄᴀᴛɪᴏɴs.\n"
    "🚀 ɴᴏ ᴜɴɴᴇᴄᴇssᴀʀʏ sᴛᴇᴘs.\n"
    "💠 ᴊᴜsᴛ ғɪʟᴇs → ʟɪɴᴋs → sʜᴀʀᴇ.\n"
    "\n"
    "╰━━━〔 🐇 ʜᴀᴠᴇ ғᴜɴ ᴡɪᴛʜ ᴛʀɪss! 〕━━━╯"
)

MAINTENANCE_TEXT = _bold(
    "🧑\u200d🔧 ᴛʀɪss ɪs ᴄᴜʀʀᴇɴᴛʟʏ ᴜɴᴅᴇʀ ᴍᴀɪɴᴛᴇɴᴀɴᴄᴇ.\n\n"
    "ᴘʟᴇᴀsᴇ ᴄʜᴇᴄᴋ ʙᴀᴄᴋ sʜᴏʀᴛʟʏ. ✨"
)

FORCE_SUB_TEXT = _bold(
    "📢 ᴊᴏɪɴ ᴏᴜʀ ᴄʜᴀɴɴᴇʟ ✨\n\n"
    "🔔 ᴛᴏ ᴄᴏɴᴛɪɴᴜᴇ ᴜsɪɴɢ ᴛʀɪss ғɪʟᴇ ᴠᴀᴜʟᴛ, ᴘʟᴇᴀsᴇ ᴊᴏɪɴ ᴛʜᴇ ʀᴇǫᴜɪʀᴇᴅ ᴄʜᴀɴɴᴇʟ(s) ʙᴇʟᴏᴡ. 🌐\n\n"
    "🌐 ᴏɴᴄᴇ ʏᴏᴜ'ᴠᴇ ᴊᴏɪɴᴇᴅ, ᴛᴀᴘ ᴄʜᴇᴄᴋ ᴊᴏɪɴᴇᴅ ᴛᴏ ᴄᴏɴᴛɪɴᴜᴇ. ✅"
)

AUTO_DELETE_NOTICE_TEXT = _bold(
    "🗑️ ᴀᴜᴛᴏ ᴅᴇʟᴇᴛᴇ ✨\n\n"
    "📩 ᴛʜɪs ᴍᴇssᴀɢᴇ ᴡɪʟʟ ʙᴇ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ ᴅᴇʟᴇᴛᴇᴅ ᴀғᴛᴇʀ ᴛʜᴇ ᴄᴏɴғɪɢᴜʀᴇᴅ ᴛɪᴍᴇ ⏳\n\n"
    "🆘 ᴘʟᴇᴀsᴇ sᴀᴠᴇ ʏᴏᴜʀ ғɪʟᴇ ʙᴇғᴏʀᴇ ᴛʜᴇ ᴛɪᴍᴇ ʟɪᴍɪᴛ. ✦"
)

LINK_EXPIRED_TEXT = _bold(
    "⌛ ᴛʜɪs ʟɪɴᴋ ʜᴀs ᴇxᴘɪʀᴇᴅ.\n\n"
    "ᴘʟᴇᴀsᴇ ʀᴇǫᴜᴇsᴛ ᴀ ɴᴇᴡ ʟɪɴᴋ ғʀᴏᴍ ᴛʜᴇ ᴏᴡɴᴇʀ. 🔁"
)

LINK_INVALID_TEXT = _bold(
    "❌ ᴛʜɪs ʟɪɴᴋ ɪs ɪɴᴠᴀʟɪᴅ ᴏʀ ʜᴀs ʙᴇᴇɴ ʀᴇᴠᴏᴋᴇᴅ."
)

SHORTENER_VERIFY_TEXT = _bold(
    "🪻ᴛʀɪss ғɪʟᴇ ᴠᴀᴜʟᴛ ⟡ ʏᴏᴜʀ ғɪʟᴇ ɪs ʀᴇᴀᴅʏ ᴛᴏ ᴀᴄᴄᴇss.\n\n"
    "ʙᴇғᴏʀᴇ ɢᴇᴛᴛɪɴɢ ᴛʜᴇ ғɪʟᴇ, ᴘʟᴇᴀsᴇ ᴄᴏᴍᴘʟᴇᴛᴇ ᴛʜᴇ sʜᴏʀᴛᴇɴᴇʀ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ 🧸.\n\n"
    "ʏᴏᴜ ʜᴀᴠᴇ ᴛʜᴇ ᴄᴏɴғɪɢᴜʀᴇᴅ ᴛɪᴍᴇ ⌛ ᴛᴏ ᴄᴏᴍᴘʟᴇᴛᴇ ɪᴛ.\n\n"
    "ɪғ ʏᴏᴜ ᴅᴏɴ'ᴛ ᴠᴇʀɪғʏ ᴡɪᴛʜɪɴ ᴛʜᴇ ᴛɪᴍᴇ ʟɪᴍɪᴛ, ᴛʜᴇ ʟɪɴᴋ ᴡɪʟʟ ᴇxᴘɪʀᴇ 🫧.\n\n"
    "ᴏɴᴄᴇ ᴠᴇʀɪғɪᴇᴅ, ʏᴏᴜʀ ғɪʟᴇ ᴡɪʟʟ ʙᴇ ᴅᴇʟɪᴠᴇʀᴇᴅ ɪᴍᴍᴇᴅɪᴀᴛᴇʟʏ 🪽"
)

SHORTENER_BYPASS_TEXT = _bold(
    "🚨ʙʏᴘᴀss ᴅᴇᴛᴇᴄᴛᴇᴅ!\n\n"
    "⟡ ᴡᴛғ — ᴍᴏᴛʜᴇʀ ғ**ᴋᴇʀ ᴡᴛғ ᴀʀᴇ ʏᴏᴜ ᴛʀʏɪɴɢ ᴛᴏ ᴅᴏ? 🖕 "
    "ᴅᴏɴ'ᴛ ᴛʀʏ ᴛᴏ ᴄʜᴇᴀᴛ ᴛʜᴇ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ sʏsᴛᴇᴍ! 🛑 "
    "ᴜsᴇ ᴛʜᴇ ᴏʀɪɢɪɴᴀʟ ʟɪɴᴋ ᴀɴᴅ ᴄᴏᴍᴘʟᴇᴛᴇ ᴛʜᴇ sʜᴏʀᴛᴇɴᴇʀ "
    "ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ ᴘʀᴏᴘᴇʀʟʏ. 🔥 "
    "ᴋᴇᴇᴘ ᴛʀʏɪɴɢ ᴛʜɪs ʙᴜʟʟsʜɪᴛ ᴀɴᴅ ʏᴏᴜ'ʟʟ ʙᴇ ᴛᴇᴍᴘᴏʀᴀʀɪʟʏ ʙʟᴏᴄᴋᴇᴅ."
)

SHORTENER_EXPIRED_TEXT = _bold(
    "⏰ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ ʟɪɴᴋ ᴇxᴘɪʀᴇᴅ.\n\n"
    "🫧 ᴛʜɪs ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ sᴇssɪᴏɴ ʜᴀs ᴇxᴘɪʀᴇᴅ."
)

SHORTENER_RATE_LIMITED_TEXT = _bold(
    "🚫 ᴛᴏᴏ ᴍᴀɴʏ ғᴀɪʟᴇᴅ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ ᴀᴛᴛᴇᴍᴘᴛs.\n\n"
    "ᴘʟᴇᴀsᴇ ᴡᴀɪᴛ ᴀ ғᴇᴡ ᴍɪɴᴜᴛᴇs ʙᴇғᴏʀᴇ ᴛʀʏɪɴɢ ᴀɢᴀɪɴ."
)

# Shown once, in place of SHORTENER_BYPASS_TEXT, on the exact attempt that
# reaches the configured strike limit (triss.database.mongodb DEFAULT_
# SETTINGS "shortener.anti_bypass.strike_limit") - deliberately does NOT
# mention how long the mute lasts (owner requirement: "time show aga
# kudathu" - don't show the time). Subsequent attempts during the mute
# window fall through to SHORTENER_RATE_LIMITED_TEXT above via the
# existing is_rate_limited() check, which is equally vague about duration.
SHORTENER_MUTED_TEXT = _bold(
    "🔇 ʏᴏᴜ'ᴠᴇ ʙᴇᴇɴ ᴛᴇᴍᴘᴏʀᴀʀɪʟʏ ᴍᴜᴛᴇᴅ ғᴏʀ ʀᴇᴘᴇᴀᴛᴇᴅ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ ʙʏᴘᴀss ᴀᴛᴛᴇᴍᴘᴛs.\n\n"
    "ᴘʟᴇᴀsᴇ sᴛᴏᴘ ᴀɴᴅ ᴛʀʏ ᴀɢᴀɪɴ ʟᴀᴛᴇʀ."
)

SHORTENER_SESSION_INVALID_TEXT = _bold(
    "❌ ᴛʜɪs ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ sᴇssɪᴏɴ ɪs ɪɴᴠᴀʟɪᴅ ᴏʀ ᴀʟʀᴇᴀᴅʏ ᴜsᴇᴅ.\n\n"
    "Pʟᴇᴀsᴇ ʀᴇᴏᴘᴇɴ ᴛʜᴇ ᴏʀɪɢɪɴᴀʟ ᴄᴏɴᴛᴇɴᴛ ʟɪɴᴋ ᴛᴏ sᴛᴀʀᴛ ᴀ ɴᴇᴡ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ."
)

SHORTENER_UNAVAILABLE_TEXT = _bold(
    "⚠️ ᴠᴇʀɪғɪᴄᴀᴛɪᴏɴ ɪs ᴛᴇᴍᴘᴏʀᴀʀɪʟʏ ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ. Pʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ sʜᴏʀᴛʟʏ."
)

# Item 5: the /start loading -> processing -> done animation (see
# triss.handlers.start._play_welcome_animation) used to be plain,
# unstyled text - now matches the rest of the bot's small-caps bold look.
WELCOME_ANIM_LOADING_TEXT = _bold("⏳ ʟᴏᴀᴅɪɴɢ...")
WELCOME_ANIM_PROCESSING_TEXT = _bold("🔄 ᴘʀᴏᴄᴇssɪɴɢ...")
WELCOME_ANIM_DONE_TEXT = _bold("✅ ᴅᴏɴᴇ!")

# Item 9/10: admin & mute management feedback text.
NOT_OWNER_TEXT = _bold("⚠️ ᴏɴʟʏ ᴛʜᴇ ᴏᴡɴᴇʀ ᴄᴀɴ ᴜsᴇ ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ.")
MUTED_USER_TEXT = _bold(
    "🔇 ʏᴏᴜ ʜᴀᴠᴇ ʙᴇᴇɴ ᴍᴜᴛᴇᴅ ʙʏ ᴛʜᴇ ᴏᴡɴᴇʀ ᴀɴᴅ ᴄᴀɴ'ᴛ ᴜsᴇ ᴛʜɪs ʙᴏᴛ ʀɪɢʜᴛ ɴᴏᴡ."
)


def mask_secret(value: Optional[str], visible: int = 4) -> str:
    """Never echo a full API token back to the owner. Shows only the last
    `visible` characters, per spec's `••••••••1234` example."""
    if not value:
        return "Not set"
    if len(value) <= visible:
        return "•" * len(value)
    return "•" * 8 + value[-visible:]


def render_welcome(template: Optional[str], *, user_id: int, first_name: str,
                    last_name: Optional[str] = None, username: Optional[str] = None) -> str:
    """Substitutes {mention}/{first}/{last}/{username}/{id} into `template`
    (the owner's custom welcome text, or DEFAULT_WELCOME_TEXT). Any HTML
    formatting already present in `template` - Bold, Italic, Underline,
    Strikethrough, Monospace, Spoiler, clickable links - is left exactly
    as-is, since only the placeholder tokens are replaced. The dynamic
    values themselves are HTML-escaped first so a display name containing
    `&`, `<` or `>` can never corrupt the surrounding template or inject
    its own markup into the message.
    """
    text = template or DEFAULT_WELCOME_TEXT
    safe_first = _escape_html_value(first_name or "there")
    safe_last = _escape_html_value(last_name) if last_name else ""
    safe_username = _escape_html_value(username) if username else ""
    mention = f'<a href="tg://user?id={user_id}">{safe_first}</a>'
    return (
        text.replace("{mention}", mention)
            .replace("{first}", safe_first)
            .replace("{last}", safe_last)
            .replace("{username}", f"@{safe_username}" if username else "")
            .replace("{id}", str(user_id))
    )

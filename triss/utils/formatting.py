"""
triss.utils.formatting
=======================
Default text templates (small-caps aesthetic, per spec), Telegram native
Markdown formatting (the Client's default parse mode - see triss/bot.py,
which sets no explicit parse_mode and therefore uses Pyrogram/Kurigram's
default Markdown parser), and the {mention}/{first}/{last}/{username}/{id}
variable substitution used in the welcome message.

This module is the single centralized place that decides how the bot's
static small-caps UI text is turned into real Telegram formatting entities
and how dynamic values get safely substituted into templates. Nothing
outside this module should hand-roll Markdown - handlers just import the
rendered constants / call render_welcome().

--------------------------------------------------------------------------
BOLD FIX
--------------------------------------------------------------------------
Unicode small-caps glyphs (the ᴀʙᴄ-style letters used throughout this file)
are just alternate letter codepoints - Telegram does not render them bold
on their own. Real bold requires an actual Bold formatting entity, which
Pyrogram's default Markdown parse mode creates from `**text**`. The
templates below did not previously contain that markup, so no Bold entity
was ever produced. `_bold()` centralizes wrapping our own fully-controlled
template strings in real `**...**` Markdown bold.

`SHORTENER_BYPASS_TEXT` is the one template that already contained a
literal `**` (as part of the censored word "f**ker"). Wrapping it in an
*additional* outer `**...**` without accounting for that would let the
inner `**` prematurely close the outer bold span and corrupt the entity
(stray literal asterisks visible in the message) - so its internal `**`
is backslash-escaped (rendered as literal asterisks) before the outer
bold is applied.

--------------------------------------------------------------------------
INJECTION / CORRUPTION FIX
--------------------------------------------------------------------------
`render_welcome()` substitutes user-controlled values (first name, last
name, username) into a template. Telegram display names can legally
contain Markdown-significant characters (asterisk, underscore, tilde,
backtick, pipe, brackets, parens, backslash) - substituting them unescaped
could both corrupt the surrounding template's formatting and let a user
inject their own formatting/links into the bot's own message.
`_escape_markdown_value()` escapes only those dynamic values, so the
static template's own intentional markup is never touched (no
double-escaping) while dynamic content can never break or hijack it.
"""

from __future__ import annotations

from typing import Optional

# Characters Pyrogram's default Markdown parser treats as formatting
# delimiters. Order matters: backslash must be escaped first, or the
# backslashes inserted for the other characters would themselves get
# re-escaped on a later pass.
_MARKDOWN_SPECIAL_CHARS = ("\\", "*", "_", "~", "`", "|", "[", "]", "(", ")")


def _escape_markdown_value(value: Optional[str]) -> str:
    """Escapes Markdown-significant characters in a single dynamic value
    (never a whole template) before it is substituted into rendered text."""
    if not value:
        return ""
    for ch in _MARKDOWN_SPECIAL_CHARS:
        value = value.replace(ch, "\\" + ch)
    return value


def _bold(text: str) -> str:
    """Wraps one of this module's own static, fully-controlled template
    strings in Telegram native Bold Markdown. Never apply this to dynamic
    or user-supplied text - use `_escape_markdown_value()` for that."""
    return f"**{text}**"


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

# NOTE: this template already contains a literal "**" (censored word) -
# that pair is escaped to \*\* here so it renders as literal asterisks
# instead of prematurely closing the outer bold span applied below.
SHORTENER_BYPASS_TEXT = _bold(
    "🚨ʙʏᴘᴀss ᴅᴇᴛᴇᴄᴛᴇᴅ!\n\n"
    "⟡ ᴡᴛғ — ᴍᴏᴛʜᴇʀ ғ\\*\\*ᴋᴇʀ ᴡᴛғ ᴀʀᴇ ʏᴏᴜ ᴛʀʏɪɴɢ ᴛᴏ ᴅᴏ? 🖕 "
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
    (the owner's custom welcome text, or DEFAULT_WELCOME_TEXT). Any
    Markdown formatting already present in `template` - Bold, Italic,
    Bold+Italic, Strikethrough, Monospace, Spoiler, clickable
    [text](url) links, plain URLs, @usernames - is left exactly as-is,
    since only the placeholder tokens are replaced. The dynamic values
    themselves are Markdown-escaped first so a display name containing
    formatting-significant characters can never corrupt the surrounding
    template or inject its own formatting/links into the message.
    """
    text = template or DEFAULT_WELCOME_TEXT
    safe_first = _escape_markdown_value(first_name or "there")
    safe_last = _escape_markdown_value(last_name) if last_name else ""
    safe_username = _escape_markdown_value(username) if username else ""
    mention = f"[{safe_first}](tg://user?id={user_id})"
    return (
        text.replace("{mention}", mention)
            .replace("{first}", safe_first)
            .replace("{last}", safe_last)
            .replace("{username}", f"@{safe_username}" if username else "")
            .replace("{id}", str(user_id))
    )
                        

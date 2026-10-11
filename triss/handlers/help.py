"""
triss.handlers.help
=====================
/help — ONLY the commands an actual end user runs (/start, /buy_premium,
/rt_save, /help itself). Owner/admin commands all live in /settings
instead (that's their own control surface) - /help stays a single,
simple view for everyone, same text for owner and regular users alike.
Small-caps + bold, matching the rest of the bot's buttons/menus.

_OWNER_ADMIN_SECTION and _SUPER_OWNER_SECTION below are NOT used by
/help any more - they're kept here only because triss.handlers.
callbacks' 💬 Comments settings button (settings:links) imports them to
show the owner the full command reference. Don't delete them without
also fixing that import.
"""

from __future__ import annotations

from pyrogram import filters
from pyrogram.types import Message

from triss.bot import app

_OWNER_ADMIN_SECTION = """
🔗 <b>Links</b>
<code>/genlink</code> — reply with the file/text/link you want, and the bot stores it and gives back one Telegram link that delivers it to anyone who opens it.
<code>/batch</code> — send the FIRST message of a range, then the LAST message, then <code>/done</code>: one link delivers everything in between in order. <code>/cancelbatch</code> cancels a batch in progress.
<code>/autobatch</code> — forward the FIRST file, then the LAST file from your base channel (or send their post links, max 200 posts): the bot copies everything in between into the Store Channel and gives one batch link.
<code>/linkdl</code> — toggles Direct Download links on/off (<code>/linkdl</code> alone just shows status; <code>/linkdl on</code> / <code>/linkdl off</code> actually change it). While on, send any file directly (no /genlink needed) and get back a real browser download link instead of a Telegram deep link. <code>/setlinkcaption your text {link}</code> customizes the reply (owner only).

👥 <b>Moderation</b> (owner or admin)
<code>/mute user_id [reason]</code> / <code>/unmute user_id</code> / <code>/listmute</code> — blocks a user from redeeming links (reply to their forwarded message instead of typing the id if you prefer).
<code>/ban user_id [reason]</code> / <code>/unban user_id</code> / <code>/listban</code> — a full block: a banned user can't use the bot at all, not even /start.

🔍 <b>Group Filters</b> (owner or admin)
<code>/search</code> — save a new filter: send the content, then its keyword/name (e.g. "Naruto"). Fires automatically on any matching message in a Connected group (⚙️ /settings -> 👥 Group Settings -> 🔗 Connect).

💎 <b>Premium</b> (owner or admin)
<code>/setvip user_id bronze|silver|gold [days]</code> — grant Premium directly, no payment needed (trials, manual approvals).
<code>/listvip</code> — list every currently active Premium user and when they expire.
<code>/deletevip user_id</code> — remove a user's Premium early.

🔓 <b>Restrict Save</b>
<code>/rt_save</code> — once enabled (⚙️ /settings -> 🔓 Restrict Save), any user verifies once then sends a t.me link to get a Restrict-Saving-protected message back unprotected.
"""

_SUPER_OWNER_SECTION = """
🛡️ <b>Admins</b> (owner only)
<code>/addadmin user_id</code> / <code>/removeadmin user_id</code> / <code>/listadmin</code> — grants/revokes admin access (same as owner except managing other admins). Reply to a forwarded message instead of typing the id if you prefer.
"""

_EVERYONE_SECTION = """
<b>ᴡᴇʟᴄᴏᴍᴇ! ʜᴇʀᴇ's ᴡʜᴀᴛ ʏᴏᴜ ᴄᴀɴ ᴅᴏ:</b>

<b>/start</b> — ᴏᴘᴇɴs ᴛʜᴇ ʙᴏᴛ / ʀᴇᴅᴇᴇᴍs ᴀ ʟɪɴᴋ sᴏᴍᴇᴏɴᴇ sʜᴀʀᴇᴅ ᴡɪᴛʜ ʏᴏᴜ.

<b>/buy_premium</b> — 💎 sᴋɪᴘ ᴛʜᴇ sʜᴏʀᴛᴇɴᴇʀ (ᴀɴᴅ ғᴏʀᴄᴇ sᴜʙ, ᴏɴ ʜɪɢʜᴇʀ ᴘʟᴀɴs) ᴡɪᴛʜ ᴀ ᴘᴀɪᴅ ᴘʟᴀɴ.

<b>/rt_save</b> — 🔓 ᴏɴᴄᴇ ᴇɴᴀʙʟᴇᴅ ʙʏ ᴛʜᴇ ᴏᴡɴᴇʀ, ᴠᴇʀɪғʏ ᴏɴᴄᴇ ᴛʜᴇɴ sᴇɴᴅ ᴀ ᴛ.ᴍᴇ ʟɪɴᴋ ғʀᴏᴍ ᴀ ʀᴇsᴛʀɪᴄᴛ-sᴀᴠɪɴɢ ᴄʜᴀᴛ ᴛᴏ ɢᴇᴛ ɪᴛ ʙᴀᴄᴋ ᴜɴᴘʀᴏᴛᴇᴄᴛᴇᴅ.

<b>/media</b> — 📱 ᴏɴᴄᴇ ᴇɴᴀʙʟᴇᴅ, sᴇɴᴅ ᴀ ʏᴏᴜᴛᴜʙᴇ/ɪɴsᴛᴀɢʀᴀᴍ/ᴛɪᴋᴛᴏᴋ/ᴛᴡɪᴛᴛᴇʀ(x)/ғᴀᴄᴇʙᴏᴏᴋ ʟɪɴᴋ ᴛᴏ ɢᴇᴛ ᴛʜᴇ ᴘᴏsᴛ ᴅᴏᴡɴʟᴏᴀᴅᴇᴅ (ᴍᴀx 50ᴍʙ).

<b>/linkdl</b> — 📥 ᴏɴᴄᴇ ᴇɴᴀʙʟᴇᴅ, sᴇɴᴅ ᴀɴʏ ғɪʟᴇ ᴅɪʀᴇᴄᴛʟʏ ᴀɴᴅ ɢᴇᴛ ᴀ ʀᴇᴀʟ ʙʀᴏᴡsᴇʀ ᴅᴏᴡɴʟᴏᴀᴅ ʟɪɴᴋ ʙᴀᴄᴋ ʀɪɢʜᴛ ᴀᴡᴀʏ — ɴᴏ ɢᴇɴʟɪɴᴋ ɴᴇᴇᴅᴇᴅ.

<b>/help</b> — ᴛʜɪs ᴍᴇɴᴜ.
"""


@app.on_message(filters.command("help") & filters.private)
async def help_cmd(client, message: Message) -> None:
    await message.reply_text(_EVERYONE_SECTION.strip())

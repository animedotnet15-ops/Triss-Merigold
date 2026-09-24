"""
triss.handlers.admin
======================
Item 9 — /addadmin /removeadmin /listadmin: manages the second trust
tier described in triss.utils.auth. Gated by deny_if_not_super_owner
(the real OWNER_ID only) so an admin can never promote/demote anyone,
including themselves.

Item 10 — /mute /unmute /listmute: an explicit, persistent block on a
user redeeming links or using the bot, independent of the Shortener's
own temporary anti-bypass mute (triss.services.shortener.is_rate_limited,
which is in-memory and self-expiring). This one is owner/admin-controlled
and gated by deny_if_not_owner (owner OR admin), since it's a moderation
action rather than an identity/privilege change.

Both command families accept either:
  - a numeric user_id as the command argument, e.g. /mute 123456789
  - replying to a forwarded message FROM the target user
"""

from __future__ import annotations

from pyrogram import filters
from pyrogram.types import Message

from triss.bot import app
from triss.database import models as db
from triss.utils.auth import deny_if_not_owner, deny_if_not_super_owner
from triss.utils import auth
from triss.services.logging_service import log_user_muted


def _resolve_target_user_id(message: Message) -> int | None:
    if len(message.command) > 1 and message.command[1].lstrip("-").isdigit():
        return int(message.command[1])
    reply = message.reply_to_message
    if reply is not None:
        if reply.forward_from is not None:
            return reply.forward_from.id
        if reply.from_user is not None:
            return reply.from_user.id
    return None


# ---------------------------------------------------------------------------
# Admin management (item 9) — super-owner only
# ---------------------------------------------------------------------------

@app.on_message(filters.command("addadmin") & filters.private)
async def addadmin_cmd(client, message: Message) -> None:
    if await deny_if_not_super_owner(message):
        return
    target_id = _resolve_target_user_id(message)
    if target_id is None:
        await message.reply_text(
            "⚠️ <b>Usage:</b> <code>/addadmin user_id</code>, or reply to a "
            "forwarded message from that user with <code>/addadmin</code>."
        )
        return
    ok = await db.add_admin(target_id, added_by=message.from_user.id)
    if not ok:
        await message.reply_text(f"⚠️ <code>{target_id}</code> is already an admin.")
        return
    auth.set_admin_cache(await db.get_admin_ids())
    await message.reply_text(
        f"✅ <code>{target_id}</code> is now an admin — same access as the owner, "
        f"except managing other admins."
    )


@app.on_message(filters.command("removeadmin") & filters.private)
async def removeadmin_cmd(client, message: Message) -> None:
    if await deny_if_not_super_owner(message):
        return
    target_id = _resolve_target_user_id(message)
    if target_id is None:
        await message.reply_text(
            "⚠️ <b>Usage:</b> <code>/removeadmin user_id</code>, or reply to a "
            "forwarded message from that user with <code>/removeadmin</code>."
        )
        return
    ok = await db.remove_admin(target_id)
    if not ok:
        await message.reply_text(f"⚠️ <code>{target_id}</code> wasn't an admin.")
        return
    auth.set_admin_cache(await db.get_admin_ids())
    await message.reply_text(f"✅ <code>{target_id}</code> is no longer an admin.")


@app.on_message(filters.command("listadmin") & filters.private)
async def listadmin_cmd(client, message: Message) -> None:
    if await deny_if_not_owner(message):
        return
    admins = await db.list_admins()
    if not admins:
        await message.reply_text("📭 No admins added yet — only the owner has access.")
        return
    lines = "\n".join(f"• <code>{a['user_id']}</code>" for a in admins)
    await message.reply_text(f"👥 <b>Admins</b> ({len(admins)}):\n\n{lines}")


# ---------------------------------------------------------------------------
# Mute management (item 10) — owner OR admin
# ---------------------------------------------------------------------------

@app.on_message(filters.command("mute") & filters.private)
async def mute_cmd(client, message: Message) -> None:
    if await deny_if_not_owner(message):
        return
    target_id = _resolve_target_user_id(message)
    if target_id is None:
        await message.reply_text(
            "⚠️ <b>Usage:</b> <code>/mute user_id [reason]</code>, or reply to a "
            "forwarded message from that user with <code>/mute [reason]</code>."
        )
        return
    # Reason is whatever comes after the user_id argument, or after the
    # command itself when replying to a forwarded message.
    parts = message.text.split(maxsplit=2)
    if len(parts) > 1 and parts[1].lstrip("-").isdigit():
        reason = parts[2] if len(parts) > 2 else None
    else:
        reason = parts[1] if len(parts) > 1 else None
    await db.mute_user(target_id, muted_by=message.from_user.id, reason=reason)
    await log_user_muted(client, target_id, message.from_user.id, reason)
    suffix = f"\nReason: {reason}" if reason else ""
    await message.reply_text(f"🔇 <code>{target_id}</code> has been muted.{suffix}")


@app.on_message(filters.command("unmute") & filters.private)
async def unmute_cmd(client, message: Message) -> None:
    if await deny_if_not_owner(message):
        return
    target_id = _resolve_target_user_id(message)
    if target_id is None:
        await message.reply_text(
            "⚠️ <b>Usage:</b> <code>/unmute user_id</code>, or reply to a "
            "forwarded message from that user with <code>/unmute</code>."
        )
        return
    ok = await db.unmute_user(target_id)
    if not ok:
        await message.reply_text(f"⚠️ <code>{target_id}</code> wasn't muted.")
        return
    await message.reply_text(f"🔊 <code>{target_id}</code> has been unmuted.")


@app.on_message(filters.command("listmute") & filters.private)
async def listmute_cmd(client, message: Message) -> None:
    if await deny_if_not_owner(message):
        return
    muted = await db.list_muted()
    if not muted:
        await message.reply_text("📭 No muted users.")
        return
    lines = []
    for m in muted:
        line = f"• <code>{m['user_id']}</code>"
        if m.get("reason"):
            line += f" — {m['reason']}"
        lines.append(line)
    await message.reply_text(f"🔇 <b>Muted users</b> ({len(muted)}):\n\n" + "\n".join(lines))

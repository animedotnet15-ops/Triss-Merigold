"""
triss.handlers.premium
========================
💎 Premium — manual UPI-payment plans, owner-approved via screenshot.

    /buy_premium  -> customizable intro (image/video/gif + caption) with
                     3 plan buttons (🥉 Bronze / 🥈 Silver / 🥇 Gold)
    tap a plan    -> scanner (UPI QR) image + UPI id + that plan's price/
                     days/details, and "send a screenshot of your payment"
    user sends a
    photo         -> forwarded to the owner with ✅ Approve / ❌ Reject
    owner taps    -> Approve grants premium (plan + days) and tells the
                     user; Reject tells the user and nothing is granted

Perks (see triss.handlers.start for where these are actually enforced):
  - ALL plans: Shortener verification is skipped entirely.
  - Silver & Gold: Force Sub is also skipped entirely.
  - Bronze: Force Sub still applies; capped at BRONZE_DAILY_LIMIT (7)
    file deliveries per IST calendar day (triss.database.models.
    increment_bronze_daily_count / get_bronze_daily_count).
"""

from __future__ import annotations

import logging

from pyrogram import filters
from pyrogram.types import CallbackQuery, Message

from triss.bot import app
from triss.config import config
from triss.database import models as db
from triss.services.cleanup import session_manager, session_is
from triss.utils.auth import owner_filter, deny_if_not_owner, deny_if_not_super_owner
from triss.utils.keyboards import (
    cancel_only, premium_menu, buy_premium_menu, premium_plan_menu, premium_approval_menu,
)

logger = logging.getLogger("triss.handlers.premium")

_COMMANDS = [
    "genlink", "batch", "done", "cancelbatch", "autobatch", "broadcast", "settings", "start",
    "addadmin", "removeadmin", "listadmin", "mute", "unmute", "listmute",
    "ban", "unban", "listban", "linkdl", "setlinkcaption", "buy_premium", "setvip", "listvip", "deletevip", "search", "rt_save", "help",
]

_PLAN_EMOJI = {"bronze": "🥉", "silver": "🥈", "gold": "🥇"}

DEFAULT_BUY_TEXT = (
    "💎 <b>Triss Premium</b>\n\n"
    "Skip the shortener (and Force Sub, on higher plans) — pick a plan below."
)


# ---------------------------------------------------------------------------
# a) /buy_premium
# ---------------------------------------------------------------------------

@app.on_message(filters.command("buy_premium") & filters.private)
async def buy_premium_cmd(client, message: Message) -> None:
    settings = await db.get_settings()
    premium = settings.get("premium", {})
    if not premium.get("scanner_file_id") or not premium.get("upi_id"):
        await message.reply_text("⚠️ Premium isn't set up yet. Please check back later.")
        return

    buy_message = premium.get("buy_message", {})
    text = buy_message.get("text") or DEFAULT_BUY_TEXT
    media_type = buy_message.get("media_type", "none")
    spoiler = bool(buy_message.get("spoiler"))
    markup = buy_premium_menu(premium.get("plans", {}))

    if media_type == "video" and buy_message.get("video_file_id"):
        await message.reply_video(buy_message["video_file_id"], caption=text,
                                   has_spoiler=spoiler, reply_markup=markup)
    elif media_type == "animation" and buy_message.get("animation_file_id"):
        await message.reply_animation(buy_message["animation_file_id"], caption=text,
                                       has_spoiler=spoiler, reply_markup=markup)
    elif media_type == "photo" and buy_message.get("photo_file_id"):
        await message.reply_photo(buy_message["photo_file_id"], caption=text,
                                   has_spoiler=spoiler, reply_markup=markup)
    else:
        await message.reply_text(text, reply_markup=markup)


# ---------------------------------------------------------------------------
# b) plan selected -> show scanner + UPI + plan details
# ---------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^buyprem:"))
async def buy_premium_router(client, cq: CallbackQuery) -> None:
    parts = cq.data.split(":")
    action = parts[1]
    user_id = cq.from_user.id

    if action == "cancel":
        session_manager.clear(user_id)
        await cq.answer("Cancelled.")
        try:
            await cq.message.delete()
        except Exception:
            pass
        return

    if action != "plan" or len(parts) < 3:
        await cq.answer()
        return
    plan_key = parts[2]

    settings = await db.get_settings()
    premium = settings.get("premium", {})
    plan = premium.get("plans", {}).get(plan_key)
    if plan is None:
        await cq.answer("Unknown plan.", show_alert=True)
        return

    session_manager.set(user_id, "premium_awaiting_screenshot", {"plan": plan_key})

    emoji = _PLAN_EMOJI.get(plan_key, "💎")
    caption = (
        f"{emoji} <b>{plan.get('label', plan_key.title())} — ₹{plan.get('price')} "
        f"({plan.get('days')} Days)</b>\n\n"
        f"{plan.get('details') or ''}\n\n"
        f"💳 UPI ID: <code>{premium.get('upi_id')}</code>\n\n"
        "Pay the amount above, then send a <b>screenshot</b> of the payment here. "
        "The owner will review and activate your plan."
    )
    scanner_id = premium.get("scanner_file_id")
    scanner_type = premium.get("scanner_media_type", "photo")
    if scanner_type == "video" and scanner_id:
        await cq.message.reply_video(scanner_id, caption=caption, reply_markup=premium_plan_menu())
    elif scanner_type == "animation" and scanner_id:
        await cq.message.reply_animation(scanner_id, caption=caption, reply_markup=premium_plan_menu())
    else:
        await cq.message.reply_photo(scanner_id, caption=caption, reply_markup=premium_plan_menu())
    await cq.answer()


# ---------------------------------------------------------------------------
# c) payment screenshot -> forward to owner for approval
# ---------------------------------------------------------------------------

@app.on_message(filters.private & session_is("premium_awaiting_screenshot") & filters.photo)
async def capture_payment_screenshot(client, message: Message) -> None:
    user = message.from_user
    session = session_manager.get(user.id)
    plan_key = (session.data.get("plan") if session else None) or "bronze"
    session_manager.clear(user.id)

    settings = await db.get_settings()
    plan = settings.get("premium", {}).get("plans", {}).get(plan_key, {})

    try:
        forwarded = await client.send_photo(
            config.owner_id, message.photo.file_id,
            caption=(
                f"💎 <b>New Premium payment</b>\n\n"
                f"User: {user.first_name or '-'} (@{user.username or '-'}, <code>{user.id}</code>)\n"
                f"Plan: {_PLAN_EMOJI.get(plan_key, '')} {plan.get('label', plan_key.title())} "
                f"— ₹{plan.get('price')} ({plan.get('days')} Days)"
            ),
        )
    except Exception:
        logger.exception("premium: could not forward payment screenshot to owner.")
        await message.reply_text("⚠️ Could not reach the owner right now. Please try again shortly.")
        return

    request_id = await db.create_premium_request(user.id, plan_key, forwarded.chat.id, forwarded.id)
    try:
        await forwarded.edit_reply_markup(premium_approval_menu(request_id))
    except Exception:
        logger.warning("premium: could not attach approval buttons.", exc_info=True)

    await message.reply_text("✅ Screenshot received! Waiting for the owner to approve your payment.")


# ---------------------------------------------------------------------------
# d) owner approves / rejects
# ---------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^prem:"))
async def premium_approval_router(client, cq: CallbackQuery) -> None:
    if await deny_if_not_super_owner(cq):
        return
    parts = cq.data.split(":")
    action, request_id = parts[1], parts[2] if len(parts) > 2 else None
    request = await db.get_premium_request(request_id) if request_id else None
    if request is None:
        await cq.answer("This request no longer exists.", show_alert=True)
        return
    if request.get("status") != "pending":
        await cq.answer(f"Already {request.get('status')}.", show_alert=True)
        return

    user_id = request["user_id"]
    plan_key = request["plan"]
    settings = await db.get_settings()
    plan = settings.get("premium", {}).get("plans", {}).get(plan_key, {})
    label = plan.get("label", plan_key.title())

    if action == "approve":
        days = int(plan.get("days", 0))
        expires_at = await db.grant_premium(user_id, plan_key, days)
        await db.set_premium_request_status(request_id, "approved")
        try:
            await client.send_message(
                user_id,
                f"🎉 Your <b>{label}</b> Premium is now active!\n\n"
                f"Valid until: {expires_at.strftime('%d-%m-%Y')}",
            )
        except Exception:
            logger.warning("premium: could not notify user %s of approval.", user_id, exc_info=True)
        await cq.answer("✅ Approved.")
        try:
            await cq.edit_message_caption(cq.message.caption + "\n\n✅ <b>Approved</b>")
        except Exception:
            pass
    elif action == "reject":
        await db.set_premium_request_status(request_id, "rejected")
        try:
            await client.send_message(
                user_id,
                f"❌ Your {label} payment screenshot was rejected. "
                "Contact the owner if you believe this is a mistake.",
            )
        except Exception:
            logger.warning("premium: could not notify user %s of rejection.", user_id, exc_info=True)
        await cq.answer("❌ Rejected.")
        try:
            await cq.edit_message_caption(cq.message.caption + "\n\n❌ <b>Rejected</b>")
        except Exception:
            pass
    else:
        await cq.answer()


# ---------------------------------------------------------------------------
# e) /setvip, /listvip, /deletevip - admin-granted premium, no payment
#    needed (trials, giveaways, manual approvals done outside the bot).
#    Same admin level as /mute and /ban.
# ---------------------------------------------------------------------------

@app.on_message(filters.command("setvip") & filters.private)
async def setvip_cmd(client, message: Message) -> None:
    if await deny_if_not_owner(message):
        return
    args = message.text.split()[1:]
    if len(args) < 2 or args[1].lower() not in ("bronze", "silver", "gold"):
        await message.reply_text(
            "Usage: <code>/setvip user_id bronze|silver|gold [days]</code>\n"
            "Days is optional - defaults to that plan's configured days."
        )
        return
    try:
        user_id = int(args[0])
    except ValueError:
        await message.reply_text("⚠️ user_id must be a numeric Telegram ID.")
        return
    plan_key = args[1].lower()
    settings = await db.get_settings()
    plan = settings.get("premium", {}).get("plans", {}).get(plan_key, {})
    if len(args) > 2:
        try:
            days = int(args[2])
        except ValueError:
            await message.reply_text("⚠️ days must be a number.")
            return
    else:
        days = int(plan.get("days", 0))
    expires_at = await db.grant_premium(user_id, plan_key, days)
    await message.reply_text(
        f"✅ Granted {_PLAN_EMOJI.get(plan_key, '')} {plan.get('label', plan_key.title())} "
        f"to <code>{user_id}</code> — valid until {expires_at.strftime('%d-%m-%Y')}."
    )
    try:
        await client.send_message(
            user_id,
            f"🎉 You've been granted <b>{plan.get('label', plan_key.title())}</b> Premium!\n\n"
            f"Valid until: {expires_at.strftime('%d-%m-%Y')}",
        )
    except Exception:
        logger.warning("setvip: could not notify user %s.", user_id, exc_info=True)


@app.on_message(filters.command("listvip") & filters.private)
async def listvip_cmd(client, message: Message) -> None:
    if await deny_if_not_owner(message):
        return
    users = await db.list_premium_users()
    if not users:
        await message.reply_text("No active Premium users right now.")
        return
    lines = ["💎 <b>Active Premium users</b>\n"]
    for u in users:
        emoji = _PLAN_EMOJI.get(u.get("plan"), "")
        lines.append(
            f"{emoji} <code>{u['user_id']}</code> — {u.get('plan', '-').title()} "
            f"(until {u['expires_at'].strftime('%d-%m-%Y')})"
        )
    await message.reply_text("\n".join(lines))


@app.on_message(filters.command("deletevip") & filters.private)
async def deletevip_cmd(client, message: Message) -> None:
    if await deny_if_not_owner(message):
        return
    args = message.text.split()[1:]
    if not args:
        await message.reply_text("Usage: <code>/deletevip user_id</code>")
        return
    try:
        user_id = int(args[0])
    except ValueError:
        await message.reply_text("⚠️ user_id must be a numeric Telegram ID.")
        return
    removed = await db.revoke_premium(user_id)
    if removed:
        await message.reply_text(f"✅ Premium removed for <code>{user_id}</code>.")
        try:
            await client.send_message(user_id, "ℹ️ Your Premium access has been removed by the owner.")
        except Exception:
            logger.warning("deletevip: could not notify user %s.", user_id, exc_info=True)
    else:
        await message.reply_text("That user doesn't have active Premium.")


# ---------------------------------------------------------------------------
# f) owner settings: scanner image, UPI id, buy message, per-plan details
# ---------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^premset:"))
async def premium_settings_router(client, cq: CallbackQuery) -> None:
    if await deny_if_not_owner(cq):
        return
    parts = cq.data.split(":")
    action = parts[1]
    user_id = cq.from_user.id

    if action == "scanner":
        session_manager.set(user_id, "premium_set_scanner")
        await cq.message.reply_text(
            "🖼️ Send the UPI QR scanner image (photo, video, or GIF) now.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "upi":
        session_manager.set(user_id, "premium_set_upi")
        await cq.message.reply_text(
            "💳 Send the UPI ID now (e.g. <code>yourname@upi</code>).",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "custommsg":
        session_manager.set(user_id, "premium_set_custommsg")
        await cq.message.reply_text(
            "✏️ Send the new /buy_premium intro — text, or a photo/video/GIF with a caption.",
            reply_markup=cancel_only("generic:cancel"),
        )
    elif action == "editplan" and len(parts) > 2:
        plan_key = parts[2]
        settings = await db.get_settings()
        plan = settings.get("premium", {}).get("plans", {}).get(plan_key, {})
        session_manager.set(user_id, "premium_set_plan", {"plan": plan_key})
        if plan_key == "bronze":
            await cq.message.reply_text(
                "✏️ Editing <b>Bronze</b>. Send 5 lines — button label, price (number), days (number), "
                "daily delivery limit (number), then details (can span several lines):\n\n"
                f"<code>{plan.get('label', 'Bronze')}\n{plan.get('price')}\n{plan.get('days')}\n"
                f"{plan.get('daily_limit', 7)}\n{plan.get('details') or ''}</code>",
                reply_markup=cancel_only("generic:cancel"),
            )
        else:
            await cq.message.reply_text(
                f"✏️ Editing <b>{plan_key.title()}</b>. Send 4 lines — button label, price (number), "
                f"days (number), then details (can span several lines):\n\n"
                f"<code>{plan.get('label', plan_key.title())}\n{plan.get('price')}\n{plan.get('days')}\n"
                f"{plan.get('details') or ''}</code>",
                reply_markup=cancel_only("generic:cancel"),
            )
        await cq.answer()
        return
    await cq.answer()


@app.on_message(filters.private & owner_filter & session_is("premium_set_scanner")
                 & (filters.photo | filters.video | filters.animation))
async def capture_premium_scanner(client, message: Message) -> None:
    session_manager.clear(message.from_user.id)
    if message.video:
        file_id, media_type = message.video.file_id, "video"
    elif message.animation:
        file_id, media_type = message.animation.file_id, "animation"
    else:
        file_id, media_type = message.photo.file_id, "photo"
    await db.update_settings({"premium.scanner_file_id": file_id, "premium.scanner_media_type": media_type})
    await message.reply_text("✅ Scanner image updated.")


@app.on_message(filters.private & owner_filter & session_is("premium_set_upi")
                 & filters.text & ~filters.command(_COMMANDS))
async def capture_premium_upi(client, message: Message) -> None:
    session_manager.clear(message.from_user.id)
    await db.update_settings({"premium.upi_id": message.text.strip()})
    await message.reply_text("✅ UPI ID updated.")


@app.on_message(filters.private & owner_filter & session_is("premium_set_custommsg")
                 & (filters.text | filters.photo | filters.video | filters.animation)
                 & ~filters.command(_COMMANDS))
async def capture_premium_custommsg(client, message: Message) -> None:
    session_manager.clear(message.from_user.id)
    text = (message.text or message.caption or None)
    text = text.html if text else None
    patch = {"premium.buy_message.text": text}
    if message.video:
        patch["premium.buy_message.media_type"] = "video"
        patch["premium.buy_message.video_file_id"] = message.video.file_id
    elif message.animation:
        patch["premium.buy_message.media_type"] = "animation"
        patch["premium.buy_message.animation_file_id"] = message.animation.file_id
    elif message.photo:
        patch["premium.buy_message.media_type"] = "photo"
        patch["premium.buy_message.photo_file_id"] = message.photo.file_id
    else:
        patch["premium.buy_message.media_type"] = "none"
    await db.update_settings(patch)
    await message.reply_text("✅ /buy_premium intro updated.")


@app.on_message(filters.private & owner_filter & session_is("premium_set_plan")
                 & filters.text & ~filters.command(_COMMANDS))
async def capture_premium_plan(client, message: Message) -> None:
    session = session_manager.get(message.from_user.id)
    plan_key = (session.data.get("plan") if session else None) or "bronze"
    is_bronze = plan_key == "bronze"
    min_lines = 4 if is_bronze else 3
    lines = message.text.html.split("\n", min_lines)
    if len(lines) < min_lines:
        extra = ", daily limit" if is_bronze else ""
        await message.reply_text(
            f"⚠️ Need at least {min_lines} lines: label, price, days{extra} "
            "(details optional on the next line+)."
        )
        return
    label = lines[0].strip()
    try:
        price = int(lines[1].strip())
        days = int(lines[2].strip())
        daily_limit = int(lines[3].strip()) if is_bronze else None
    except ValueError:
        extra = ", daily limit (line 4)" if is_bronze else ""
        await message.reply_text(f"⚠️ Price (line 2), days (line 3){extra} must be plain numbers.")
        return
    details_idx = 4 if is_bronze else 3
    details = lines[details_idx].strip() if len(lines) > details_idx else ""
    session_manager.clear(message.from_user.id)
    patch = {
        f"premium.plans.{plan_key}.label": label,
        f"premium.plans.{plan_key}.price": price,
        f"premium.plans.{plan_key}.days": days,
        f"premium.plans.{plan_key}.details": details,
    }
    extra_msg = ""
    if is_bronze:
        patch[f"premium.plans.{plan_key}.daily_limit"] = daily_limit
        extra_msg = f", {daily_limit}/day"
    await db.update_settings(patch)
    await message.reply_text(f"✅ {plan_key.title()} updated — {label}, ₹{price}, {days} days{extra_msg}.")

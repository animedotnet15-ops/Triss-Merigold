"""
triss.handlers.earn
=====================
💰 Earn$ System — gamified points/EXP for Connected groups (the same
"group" kind in the `channels` collection that 🔍 Group Filters uses -
Settings -> 👥 Group Settings -> 🔗 Connect is shared by both features).

  - 📝 Daily Quote: posted every `quote_interval_minutes`; first member
    to reply with the correct answer wins quote_points + quote_exp, and
    a 🎁 Exclusive Drop code (12 digits) sent to their PM.
  - 🎭 Mysterious Drop: posted `drops_per_day` times/day; same
    first-correct-reply mechanic, reusing the same quote bank, but the
    points are boosted by `drop_bonus_percent`, and a copy of the drop
    goes to the owner's PM the moment it's posted in the group.
  - 🎁 Exclusive Drop codes (from winning either of the above) redeem via
    /redeem for a RANDOM reward - usually points or EXP, rarely
    (`redeem_rare_chance_percent`) the rare perk: `redeem_rare_hours` of
    no Shortener / no Force Sub (exactly like Premium Silver/Gold, just
    free and time-limited - see triss.handlers.start's
    _premium_skips_force_sub / _skips_shortener_entirely).
  - 🎟️ Coupon codes (owner-generated, Settings -> 💰 Earn$ System ->
    Coupon) always grant that same rare perk for an owner-chosen
    duration - never points/EXP - and get posted ("dropped") to every
    Connected group for whoever redeems first.
  - Every Sunday (checked once per scheduler tick, first time that day):
    weekly_points resets to 0 for everyone, a Rankboard (top 10 by the
    week's points) posts to every Connected group, and the top 3
    automatically get the rare perk for rank_rewards_hours[1]/[2]/[3].

Every code is single-use - triss.database.models.redeem_earn_code claims
it atomically, so a race between two people redeeming the same code
can never double-pay it.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime

from pyrogram import filters
from pyrogram.types import CallbackQuery, Message

from triss.bot import app
from triss.config import config
from triss.database import models as db
from triss.services.cleanup import session_manager, session_is
from triss.utils.auth import owner_filter, deny_if_not_owner

logger = logging.getLogger("triss.handlers.earn")

_COMMANDS = [
    "genlink", "batch", "done", "cancelbatch", "autobatch", "broadcast", "settings", "start",
    "addadmin", "removeadmin", "listadmin", "mute", "unmute", "listmute",
    "ban", "unban", "listban", "linkdl", "setlinkcaption", "buy_premium",
    "setvip", "listvip", "deletevip", "search", "rt_save", "media", "delfilter",
    "profile", "redeem", "givepoints", "removepoints", "addquote", "help",
]

_RANK_EMOJIS = ["🥇", "🥈", "🥉"]


# ---------------------------------------------------------------------------
# a) /profile - group only, Connected groups only
# ---------------------------------------------------------------------------

@app.on_message(filters.command("profile") & filters.group)
async def profile_cmd(client, message: Message) -> None:
    settings = await db.get_settings()
    if not settings.get("earn_system", {}).get("enabled"):
        return
    connected = await db.list_channels("group")
    if not any(g["chat_id"] == message.chat.id for g in connected):
        return

    user = message.from_user
    if user is None:
        return
    earn_user = await db.get_earn_user(user.id)
    username = f"@{user.username}" if user.username else "-"
    text = (
        f"👤 <b>Profile</b>\n\n"
        f"Name: {user.first_name or '-'}\n"
        f"Username: {username}\n"
        f"ID: <code>{user.id}</code>\n"
        f"Points: {earn_user.get('points', 0)}\n"
        f"Lv: {earn_user.get('level', 1)}\n"
        f"Exp: {earn_user.get('exp', 0)}"
    )
    photo = user.photo
    if photo:
        try:
            await client.send_photo(message.chat.id, photo.big_file_id, caption=text)
            return
        except Exception:
            logger.debug("earn: could not send profile photo, falling back to text.", exc_info=True)
    await message.reply_text(text)


# ---------------------------------------------------------------------------
# b) /redeem - PM only
# ---------------------------------------------------------------------------

@app.on_message(filters.command("redeem") & filters.private)
async def redeem_cmd(client, message: Message) -> None:
    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.reply_text("Usage: <code>/redeem code</code>")
        return
    code = args[1].strip()
    user_id = message.from_user.id

    doc = await db.redeem_earn_code(code, user_id)
    if doc is None:
        await message.reply_text("⚠️ That code is invalid or has already been used.")
        return

    reward_type = doc["reward_type"]
    value = doc["reward_value"]
    if reward_type == "points":
        await db.add_points_and_exp(user_id, value, 0)
        await message.reply_text(f"🎉 Redeemed! +{value} points.")
    elif reward_type == "exp":
        await db.add_points_and_exp(user_id, 0, value)
        await message.reply_text(f"🎉 Redeemed! +{value} EXP.")
    elif reward_type == "perk_hours":
        expires_at = await db.grant_earn_perk(user_id, value)
        await message.reply_text(
            "🎉 <b>RARE REWARD!</b>\n\nNo Shortener / No Force Sub until "
            f"{expires_at.strftime('%d-%m-%Y %H:%M UTC')}."
        )
    else:
        await message.reply_text("🎉 Redeemed!")


# ---------------------------------------------------------------------------
# c) answering a Daily Quote / Mysterious Drop in a Connected group
# ---------------------------------------------------------------------------

def _pick_code_reward(es: dict) -> tuple[str, float]:
    rare_chance = es.get("redeem_rare_chance_percent", 10)
    if random.random() * 100 < rare_chance:
        return "perk_hours", es.get("redeem_rare_hours", 6)
    if random.random() < 0.5:
        return "points", random.randint(5, 20)
    return "exp", random.randint(5, 15)


@app.on_message(filters.group & filters.text & ~filters.via_bot & ~filters.me)
async def earn_answer_listener(client, message: Message) -> None:
    settings = await db.get_settings()
    es = settings.get("earn_system", {})
    if not es.get("enabled"):
        return
    challenge = await db.get_active_challenge(message.chat.id)
    if challenge is None:
        return
    if (message.text or "").strip().lower() != challenge["answer"]:
        return

    # First correct reply wins - clear immediately so a near-simultaneous
    # second correct reply can't also win the same challenge.
    await db.clear_active_challenge(message.chat.id)

    user = message.from_user
    points, exp = es.get("quote_points", 10), es.get("quote_exp", 5)
    if challenge["kind"] == "drop":
        bonus = es.get("drop_bonus_percent", 2)
        points = round(points * (1 + bonus / 100))

    await db.add_points_and_exp(user.id, points, exp)
    reward_type, reward_value = _pick_code_reward(es)
    code = await db.create_earn_code(reward_type, reward_value, source=challenge["kind"])

    label = "📝 Daily Quote" if challenge["kind"] == "quote" else "🎭 Mysterious Drop"
    await message.reply_text(
        f"✅ <b>Correct!</b> {label} won by {user.first_name or 'someone'} — "
        f"+{points} points, +{exp} EXP!"
    )
    try:
        await client.send_message(
            user.id,
            f"🎁 <b>Exclusive Drop code!</b>\n\n<code>{code}</code>\n\n"
            f"Redeem it with <code>/redeem {code}</code>.",
        )
    except Exception:
        logger.debug("earn: could not DM the exclusive code to %s (never started the bot?).", user.id)


# ---------------------------------------------------------------------------
# d) scheduler - Daily Quote / Mysterious Drop posting + weekly reset
# ---------------------------------------------------------------------------

async def _post_quote(client, group_id: int) -> None:
    quote = await db.get_random_quote()
    if quote is None:
        return
    try:
        msg = await client.send_message(
            group_id, f"📝 <b>Daily Quote!</b>\n\n{quote['question']}\n\nFirst correct reply wins!"
        )
    except Exception:
        logger.warning("earn: could not post quote to group %s.", group_id, exc_info=True)
        return
    await db.set_active_challenge(group_id, "quote", quote["question"], quote["answer"], msg.id)


async def _post_drop(client, group_id: int) -> None:
    quote = await db.get_random_quote()
    if quote is None:
        return
    try:
        msg = await client.send_message(
            group_id,
            f"🎭 <b>Mysterious Drop!</b>\n\n{quote['question']}\n\nFirst correct reply wins bonus rewards!",
        )
    except Exception:
        logger.warning("earn: could not post drop to group %s.", group_id, exc_info=True)
        return
    await db.set_active_challenge(group_id, "drop", quote["question"], quote["answer"], msg.id)
    if config.owner_id:
        try:
            await client.send_message(
                config.owner_id,
                f"🎭 Mysterious Drop posted in <code>{group_id}</code>:\n\n"
                f"{quote['question']}\n(Answer: {quote['answer']})",
            )
        except Exception:
            logger.debug("earn: could not copy drop to owner PM.", exc_info=True)


async def _maybe_weekly_reset(client) -> None:
    now = datetime.utcnow()
    if now.weekday() != 6:  # Sunday
        return
    iso = now.isocalendar()
    week_key = f"{iso[0]}-W{iso[1]}"
    if await db.get_earn_last_weekly_reset() == week_key:
        return

    settings = await db.get_settings()
    es = settings.get("earn_system", {})
    leaderboard = await db.get_weekly_leaderboard(limit=10)
    groups = await db.list_channels("group")

    lines = ["🏆 <b>Weekly Rankboard</b>\n"]
    if not leaderboard:
        lines.append("No activity this week.")
    for i, u in enumerate(leaderboard, start=1):
        try:
            real_user = await client.get_users(u["user_id"])
            name = real_user.first_name or "-"
        except Exception:
            name = str(u["user_id"])
        emoji = _RANK_EMOJIS[i - 1] if i <= 3 else "🏅"
        lines.append(f"{emoji} {name} (<code>{u['user_id']}</code>) — {u.get('weekly_points', 0)} pts")
    text = "\n".join(lines)

    for g in groups:
        try:
            await client.send_message(g["chat_id"], text)
        except Exception:
            logger.warning("earn: could not post rankboard to %s.", g["chat_id"], exc_info=True)

    rank_hours = es.get("rank_rewards_hours", {"1": 30, "2": 24, "3": 12})
    for i, u in enumerate(leaderboard[:3], start=1):
        hours = rank_hours.get(str(i))
        if not hours:
            continue
        expires_at = await db.grant_earn_perk(u["user_id"], hours)
        try:
            await client.send_message(
                u["user_id"],
                f"🏆 You ranked #{i} on this week's Rankboard!\n\n"
                f"No Shortener / No Force Sub until {expires_at.strftime('%d-%m-%Y %H:%M UTC')}.",
            )
        except Exception:
            logger.debug("earn: could not notify rank #%d winner %s.", i, u["user_id"], exc_info=True)

    await db.reset_weekly_points()
    await db.set_earn_last_weekly_reset(week_key)


async def _scheduler_tick() -> None:
    settings = await db.get_settings()
    es = settings.get("earn_system", {})
    if not es.get("enabled"):
        return
    groups = await db.list_channels("group")
    if groups:
        now = time.time()
        quote_interval = max(1, es.get("quote_interval_minutes", 60)) * 60
        drops_per_day = max(1, es.get("drops_per_day", 2))
        slot_seconds = 86400 / drops_per_day
        current_slot = str(int(now // slot_seconds))

        for g in groups:
            group_id = g["chat_id"]
            if await db.get_active_challenge(group_id) is not None:
                continue  # one open challenge at a time per group

            state = await db.get_earn_group_state(group_id)
            if now - state.get("last_quote_at", 0) >= quote_interval:
                await _post_quote(app, group_id)
                await db.set_earn_group_last_quote(group_id, now)
                continue
            if state.get("last_drop_slot") != current_slot:
                await _post_drop(app, group_id)
                await db.set_earn_group_last_drop_slot(group_id, current_slot)

    await _maybe_weekly_reset(app)


async def earn_scheduler_loop(interval_seconds: int = 60) -> None:
    """Runs for the lifetime of the process; cancelled cleanly on
    shutdown (see triss.bot's startup/shutdown, same pattern as
    triss.services.cleanup.periodic_cleanup_loop)."""
    logger.info("Starting Earn$ System scheduler loop (interval=%ss).", interval_seconds)
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await _scheduler_tick()
            except Exception:
                logger.exception("Error during Earn$ scheduler tick (continuing).")
    except asyncio.CancelledError:
        logger.info("Earn$ scheduler loop cancelled; shutting down cleanly.")
        raise


# ---------------------------------------------------------------------------
# e) owner/admin: give/remove points, add quote to bank
# ---------------------------------------------------------------------------

@app.on_message(filters.command("givepoints") & filters.private)
async def givepoints_cmd(client, message: Message) -> None:
    if await deny_if_not_owner(message):
        return
    args = message.text.split()[1:]
    if len(args) < 2:
        await message.reply_text("Usage: <code>/givepoints user_id amount</code>")
        return
    try:
        user_id, amount = int(args[0]), int(args[1])
    except ValueError:
        await message.reply_text("⚠️ user_id and amount must be numbers.")
        return
    await db.adjust_points(user_id, amount)
    await message.reply_text(f"✅ Gave {amount} points to <code>{user_id}</code>.")


@app.on_message(filters.command("removepoints") & filters.private)
async def removepoints_cmd(client, message: Message) -> None:
    if await deny_if_not_owner(message):
        return
    args = message.text.split()[1:]
    if len(args) < 2:
        await message.reply_text("Usage: <code>/removepoints user_id amount</code>")
        return
    try:
        user_id, amount = int(args[0]), int(args[1])
    except ValueError:
        await message.reply_text("⚠️ user_id and amount must be numbers.")
        return
    await db.adjust_points(user_id, -amount)
    await message.reply_text(f"✅ Removed {amount} points from <code>{user_id}</code>.")


@app.on_message(filters.command("addquote") & filters.private)
async def addquote_cmd(client, message: Message) -> None:
    if await deny_if_not_owner(message):
        return
    session_manager.set(message.from_user.id, "earn_add_quote")
    await message.reply_text(
        "✏️ Send the quote now as 2 lines — the question, then the answer:\n\n"
        "<code>What's the capital of France?\nparis</code>",
    )


@app.on_message(filters.private & owner_filter & session_is("earn_add_quote")
                 & filters.text & ~filters.command(_COMMANDS))
async def capture_add_quote(client, message: Message) -> None:
    lines = message.text.split("\n", 1)
    session_manager.clear(message.from_user.id)
    if len(lines) < 2 or not lines[1].strip():
        await message.reply_text("⚠️ Need 2 lines: question, then answer. Run /addquote again.")
        return
    await db.add_quote_to_bank(lines[0].strip(), lines[1].strip())
    await message.reply_text("✅ Quote added to the bank.")


# ---------------------------------------------------------------------------
# f) owner settings: toggle, config, quote bank, coupon
# ---------------------------------------------------------------------------

from triss.utils.keyboards import (  # noqa: E402
    earn_menu, earn_quote_bank_menu, earn_coupon_menu, earn_coupon_ready_menu, cancel_only,
)


@app.on_callback_query(filters.regex(r"^earn:"))
async def earn_settings_router(client, cq: CallbackQuery) -> None:
    if await deny_if_not_owner(cq):
        return
    parts = cq.data.split(":")
    action = parts[1]
    user_id = cq.from_user.id

    if action == "toggle":
        settings = await db.get_settings()
        new_value = not settings.get("earn_system", {}).get("enabled", False)
        await db.update_settings({"earn_system.enabled": new_value})
        await cq.answer(f"Public Use {'enabled' if new_value else 'disabled'}.", show_alert=True)
        settings = await db.get_settings()
        await _edit_settings_menu(cq, settings.get("earn_system", {}))
        return

    if action == "config":
        settings = await db.get_settings()
        es = settings.get("earn_system", {})
        session_manager.set(user_id, "earn_set_config")
        await cq.message.reply_text(
            "⚙️ Send 5 lines — quote points, quote EXP, drop bonus % , quote interval (minutes), "
            "drops per day:\n\n"
            f"<code>{es.get('quote_points', 10)}\n{es.get('quote_exp', 5)}\n"
            f"{es.get('drop_bonus_percent', 2)}\n{es.get('quote_interval_minutes', 60)}\n"
            f"{es.get('drops_per_day', 2)}</code>",
            reply_markup=cancel_only("generic:cancel"),
        )
        await cq.answer()
        return

    if action == "quotebank":
        quotes = await db.list_quote_bank()
        if not quotes:
            await cq.answer("No quotes yet - add one with /addquote in PM.", show_alert=True)
            return
        menu_quotes = [{"question": q["question"], "_id": str(q["_id"])} for q in quotes]
        await cq.message.edit_text(f"📚 <b>Quote Bank</b> ({len(quotes)})\n\nTap to remove. Add more with /addquote.",
                                    reply_markup=earn_quote_bank_menu(menu_quotes))
        await cq.answer()
        return

    if action == "rmquote" and len(parts) > 2:
        await db.remove_quote_from_bank(parts[2])
        await cq.answer("Removed.")
        quotes = await db.list_quote_bank()
        menu_quotes = [{"question": q["question"], "_id": str(q["_id"])} for q in quotes]
        if menu_quotes:
            await cq.message.edit_text(f"📚 <b>Quote Bank</b> ({len(menu_quotes)})\n\nTap to remove.",
                                        reply_markup=earn_quote_bank_menu(menu_quotes))
        else:
            await cq.message.edit_text("📚 <b>Quote Bank</b> — empty. Add one with /addquote in PM.",
                                        reply_markup=earn_coupon_menu())
        return

    if action == "coupon":
        await cq.message.edit_text(
            "🎟️ <b>Coupon</b>\n\nGenerates a code that always grants the rare "
            "no-Shortener/no-Force-Sub perk, for whatever duration you pick.",
            reply_markup=earn_coupon_menu(),
        )
        await cq.answer()
        return

    if action == "gencoupon":
        session_manager.set(user_id, "earn_coupon_hours")
        await cq.message.reply_text(
            "🎟️ Send the perk duration in whole hours (e.g. <code>24</code>).",
            reply_markup=cancel_only("generic:cancel"),
        )
        await cq.answer()
        return

    if action == "sendcoupon" and len(parts) > 2:
        code = parts[2]
        groups = await db.list_channels("group")
        if not groups:
            await cq.answer("No Connected groups to drop it in.", show_alert=True)
            return
        sent = 0
        for g in groups:
            try:
                await client.send_message(
                    g["chat_id"],
                    f"🎟️ <b>Coupon Drop!</b>\n\nFirst to redeem gets a rare reward:\n\n"
                    f"<code>/redeem {code}</code> (in the bot's PM)",
                )
                sent += 1
            except Exception:
                logger.warning("earn: could not drop coupon to %s.", g["chat_id"], exc_info=True)
        await cq.answer(f"Dropped to {sent} group(s).", show_alert=True)
        return

    await cq.answer()


async def _edit_settings_menu(cq: CallbackQuery, es: dict) -> None:
    await cq.message.edit_text("💰 <b>Earn$ System</b>", reply_markup=earn_menu(es))


@app.on_message(filters.private & owner_filter & session_is("earn_set_config")
                 & filters.text & ~filters.command(_COMMANDS))
async def capture_earn_config(client, message: Message) -> None:
    lines = message.text.strip().split("\n")
    if len(lines) < 5:
        await message.reply_text("⚠️ Need 5 lines: points, EXP, bonus %, interval (minutes), drops/day.")
        return
    try:
        points, exp, bonus, interval, drops = (int(x.strip()) for x in lines[:5])
    except ValueError:
        await message.reply_text("⚠️ All 5 values must be whole numbers.")
        return
    session_manager.clear(message.from_user.id)
    await db.update_settings({
        "earn_system.quote_points": points,
        "earn_system.quote_exp": exp,
        "earn_system.drop_bonus_percent": bonus,
        "earn_system.quote_interval_minutes": max(1, interval),
        "earn_system.drops_per_day": max(1, drops),
    })
    await message.reply_text("✅ Earn$ System config updated.")


@app.on_message(filters.private & owner_filter & session_is("earn_coupon_hours")
                 & filters.text & ~filters.command(_COMMANDS))
async def capture_earn_coupon_hours(client, message: Message) -> None:
    text = message.text.strip()
    if not text.isdigit() or int(text) < 1:
        await message.reply_text("⚠️ Send a whole number of hours, e.g. <code>24</code>.")
        return
    session_manager.clear(message.from_user.id)
    hours = int(text)
    code = await db.create_earn_code("perk_hours", hours, source="coupon", created_by=message.from_user.id)
    await message.reply_text(
        f"✅ Coupon generated — grants {hours}h no-Shortener/no-Force-Sub.\n\n"
        f"Code: <code>{code}</code>\n\nTap below to drop it to every Connected group.",
        reply_markup=earn_coupon_ready_menu(code),
    )

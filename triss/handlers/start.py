"""
triss.handlers.start
=====================
Handles three distinct flows behind the single /start command, exactly
as Telegram deep links work:

  /start                        -> plain welcome (animated intro + welcome message)
  /start <token>                 -> resolve a content token: validate the
                                    link, check expiry, check Force Sub,
                                    then either deliver directly (Shortener
                                    OFF) or start a new verification
                                    session (Shortener ON)
  /start verify_<session_id><proof>  -> the shortener redirected here
                                     directly with the session's proof
                                     (see triss.services.shortener); the
                                     proof is validated server-side before
                                     anything is delivered, flagged as a
                                     bypass, or reported expired

Order of checks for a content token, per spec: token validity -> link
expiration -> Force Sub -> Shortener verification -> delivery.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from pyrogram import filters
from pyrogram.types import Message, LinkPreviewOptions
from pyrogram.errors import RPCError

from triss.bot import app
from triss.database import models as db
from triss.services import forcesub, shortener
from triss.handlers.linkdl import deliver_linkdl, LINKDL_START_PREFIX
from triss.services.cleanup import session_manager, session_is
from triss.services.delivery import deliver_and_schedule, schedule_auto_delete
from triss.services.logging_service import log_bot_start, log_verified, log_bypass_detected
from triss.utils.formatting import (
    DEFAULT_WELCOME_TEXT,
    FORCE_SUB_TEXT,
    LINK_EXPIRED_TEXT,
    LINK_INVALID_TEXT,
    SHORTENER_VERIFY_TEXT,
    SHORTENER_BYPASS_TEXT,
    SHORTENER_EXPIRED_TEXT,
    SHORTENER_MUTED_TEXT,
    SHORTENER_RATE_LIMITED_TEXT,
    SHORTENER_SESSION_INVALID_TEXT,
    SHORTENER_UNAVAILABLE_TEXT,
    WELCOME_ANIM_LOADING_TEXT,
    WELCOME_ANIM_PROCESSING_TEXT,
    WELCOME_ANIM_DONE_TEXT,
    MUTED_USER_TEXT,
    BANNED_USER_TEXT,
    render_welcome,
)
from triss.utils.keyboards import (
    force_sub_user_keyboard,
    shortener_verification_keyboard,
    shortener_retry_keyboard,
    format_duration,
)
from triss.utils.tokens import is_plausible_token, build_deep_link

logger = logging.getLogger("triss.handlers.start")

_SPEED_DELAYS = {
    "slow": (1.6, 1.6, 1.6),
    "default": (0.9, 0.9, 0.9),
    "speed": (0.35, 0.35, 0.35),
}

VERIFY_PREFIX = "verify_"


async def send_welcome_media(message: Message, welcome: dict, text: str) -> Message:
    """Item 8: welcome media was photo-only before — now sends whichever
    of photo/video/animation(gif) is configured via welcome.media_type
    (default "photo", so every pre-existing configured welcome photo
    keeps working unchanged). Shared by the real /start flow here and by
    triss.handlers.callbacks' welcome:preview action, so both stay in
    sync automatically.

    Uses app.send_* (Client-level) rather than message.reply_* (the
    bound-method shortcuts) for consistency with the rest of this
    module's media-sending helpers."""
    chat_id = message.chat.id
    media_type = welcome.get("media_type", "photo")
    spoiler = bool(welcome.get("spoiler"))
    if media_type == "video" and welcome.get("video_file_id"):
        return await app.send_video(chat_id=chat_id, video=welcome["video_file_id"], caption=text,
                                     has_spoiler=spoiler)
    if media_type == "animation" and welcome.get("animation_file_id"):
        return await app.send_animation(chat_id=chat_id, animation=welcome["animation_file_id"], caption=text,
                                         has_spoiler=spoiler)
    if media_type == "photo" and welcome.get("photo_file_id"):
        return await app.send_photo(chat_id=chat_id, photo=welcome["photo_file_id"], caption=text,
                                     has_spoiler=spoiler)
    return await app.send_message(chat_id=chat_id, text=text,
                                   link_preview_options=LinkPreviewOptions(is_disabled=True))


async def _play_welcome_animation(message: Message, speed: str) -> Message:
    delays = _SPEED_DELAYS.get(speed, _SPEED_DELAYS["default"])
    status = await message.reply_text(WELCOME_ANIM_LOADING_TEXT)
    await asyncio.sleep(delays[0])
    try:
        await status.edit_text(WELCOME_ANIM_PROCESSING_TEXT)
    except RPCError:
        pass
    await asyncio.sleep(delays[1])
    try:
        await status.edit_text(WELCOME_ANIM_DONE_TEXT)
    except RPCError:
        pass
    await asyncio.sleep(delays[2])
    return status


async def _send_welcome(message: Message, settings: dict) -> None:
    """Required order (per spec): sticker first -> exactly a 3-second
    pause -> then the (optionally animated) welcome message/photo. The
    sticker is sent at most once per /start, and the 3-second pause uses
    `asyncio.sleep`, so only this single /start's handler coroutine
    waits — the rest of the bot/event loop keeps handling other updates
    normally the whole time."""
    welcome = settings.get("welcome", {})
    sent_ids: list[int] = []

    if welcome.get("sticker_enabled") and welcome.get("sticker_file_id"):
        try:
            sticker_msg = await message.reply_sticker(welcome["sticker_file_id"])
        except RPCError:
            logger.warning("Failed to send configured welcome sticker.", exc_info=True)
        else:
            sent_ids.append(sticker_msg.id)
            # Exact 3-second delay between sticker and welcome message,
            # only after the sticker was actually sent successfully -
            # non-blocking, so it never stalls the bot for other users.
            await asyncio.sleep(3)

    speed = welcome.get("animation_speed", "default")
    status = await _play_welcome_animation(message, speed)

    user = message.from_user
    text = render_welcome(
        welcome.get("text") or DEFAULT_WELCOME_TEXT,
        user_id=user.id,
        first_name=user.first_name or "there",
        last_name=user.last_name,
        username=user.username,
    )

    try:
        await status.delete()
    except RPCError:
        pass

    sent = await send_welcome_media(message, welcome, text)
    sent_ids.append(sent.id)

    await schedule_auto_delete(app, message.chat.id, sent_ids)


async def _handle_plain_start(message: Message, settings: dict) -> None:
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name, user.last_name)
    await log_bot_start(app, user.id, user.username, user.first_name, has_token=False)
    await _send_welcome(message, settings)


async def _bot_username(client) -> str:
    return getattr(client, "username", None) or (await client.get_me()).username


async def _send_customizable_popup(client, message: Message, popup_config: dict,
                                    fallback_text: str, reply_markup=None) -> None:
    """Shared sender for the owner-customizable popups (verify_message /
    bypass_message / muted_message — see triss.database.mongodb
    DEFAULT_SETTINGS and triss.handlers.callbacks' shortener:{verifymsg,
    bypassmsg,mutedmsg} / sysaccess:{verifymsg,bypassmsg,mutedmsg}
    submenus). `popup_config` is one of those dicts: {"text",
    "photo_file_id", "spoiler"}. An empty/missing text falls back to
    `fallback_text` (the hardcoded default in triss.utils.formatting)."""
    popup_config = popup_config or {}
    text = popup_config.get("text") or fallback_text
    photo_id = popup_config.get("photo_file_id")
    if photo_id:
        await client.send_photo(
            chat_id=message.chat.id, photo=photo_id, caption=text,
            has_spoiler=bool(popup_config.get("spoiler")),
            reply_markup=reply_markup,
        )
    else:
        await client.send_message(
            chat_id=message.chat.id, text=text, reply_markup=reply_markup,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )


async def _begin_shortener_verification(client, message: Message, token: str, shortener_settings: dict,
                                         mode: str = "per_link") -> None:
    user_id = message.from_user.id
    anti_bypass = shortener_settings.get("anti_bypass", {})
    strike_limit = int(anti_bypass.get("strike_limit", 3))
    mute_seconds = int(anti_bypass.get("mute_seconds", 600))

    if shortener.is_rate_limited(user_id, threshold=strike_limit, window_seconds=mute_seconds):
        await message.reply_text(SHORTENER_RATE_LIMITED_TEXT)
        return

    username = await _bot_username(client)
    session, short_url = await shortener.start_new_verification(
        username, user_id, token, shortener_settings, mode=mode
    )

    if short_url is None:
        logger.error("Could not generate a shortener link for user %s (token=%s, mode=%s).", user_id, token, mode)
        await message.reply_text(SHORTENER_UNAVAILABLE_TEXT)
        return

    tutorial_url = shortener_settings.get("tutorial_url")
    await _send_customizable_popup(
        client, message, shortener_settings.get("verify_message", {}), SHORTENER_VERIFY_TEXT,
        reply_markup=shortener_verification_keyboard(short_url, tutorial_url),
    )


async def _premium_skips_force_sub(user_id: int) -> bool:
    """💎 Premium: Silver and Gold skip Force Sub entirely. Bronze does
    NOT - only Shortener is skipped for Bronze (see
    triss.handlers.premium's module docstring)."""
    premium = await db.get_premium(user_id)
    return bool(premium) and premium.get("plan") in ("silver", "gold")


async def _bronze_limit_reached(user_id: int) -> bool:
    """True only for a Bronze user who has used all of today's (IST)
    deliveries. Checked BEFORE deciding whether to skip Shortener at all
    - once the cap is hit, Bronze falls through to the normal Shortener
    flow (same as a non-premium user) instead of being blocked outright,
    so the user can still get the file by just completing verification."""
    premium = await db.get_premium(user_id)
    if not premium or premium.get("plan") != "bronze":
        return False
    settings = await db.get_settings()
    limit = settings.get("premium", {}).get("plans", {}).get("bronze", {}).get("daily_limit", db.BRONZE_DAILY_LIMIT)
    return await db.get_bronze_daily_count(user_id) >= limit


async def _deliver_with_premium_gate(client, message: Message, user_id: int, link_doc: dict) -> None:
    """Delivers link_doc. For an under-the-cap Bronze user this also
    counts the delivery against today's limit (👑 Premium - see
    triss.handlers.premium and triss.database.models). Silver/Gold and
    non-premium users have no cap here."""
    premium = await db.get_premium(user_id)
    delivered = await deliver_and_schedule(client, user_id, link_doc)
    if not delivered:
        await message.reply_text("⚠️ Sorry, this content could not be delivered right now.")
        return
    if premium and premium.get("plan") == "bronze":
        await db.increment_bronze_daily_count(user_id)


async def continue_after_force_sub(client, message: Message, user_id: int, token: str, settings: dict) -> None:
    """Shared continuation used both by the normal token flow (once Force
    Sub is already satisfied) and by the Force-Sub 'Verify' button (once
    Force Sub becomes satisfied) — so a resumed access goes through
    Shortener/System Access verification exactly like a fresh one,
    instead of skipping it.

    Old Method and System Access are mutually exclusive (enforced in
    triss.handlers.callbacks' toggle handlers), so at most one of the
    two branches below is ever reachable at a time — checked here in
    priority order: an already-active System Access grant always wins
    (no point re-verifying someone who's already unlocked), then a new
    System Access verification, then Old Method, then no gating at all."""
    link_doc = await db.get_link(token)
    if link_doc is None or link_doc.get("revoked"):
        await message.reply_text(LINK_INVALID_TEXT)
        return
    expires_at = link_doc.get("expires_at")
    if expires_at is not None and expires_at < time.time():
        await message.reply_text(LINK_EXPIRED_TEXT)
        return

    # 💎 Premium: every plan (Bronze/Silver/Gold) skips Shortener
    # verification entirely - see triss.handlers.premium. EXCEPTION:
    # once a Bronze user has used today's full quota, they drop through
    # to the normal flow below (System Access / Shortener / nothing)
    # exactly like a non-premium user, instead of being blocked outright.
    premium = await db.get_premium(user_id)
    if premium and not await _bronze_limit_reached(user_id):
        await _deliver_with_premium_gate(client, message, user_id, link_doc)
        return

    shortener_settings = settings.get("shortener", {})
    system_access_settings = shortener_settings.get("system_access", {})

    if system_access_settings.get("enabled"):
        if await db.has_active_system_access(user_id):
            # Unlimited-access window already active — skip verification
            # entirely for this and every other link until it expires.
            await _deliver_with_premium_gate(client, message, user_id, link_doc)
            return
        await _begin_shortener_verification(client, message, token, system_access_settings, mode="system_access")
        return

    if shortener_settings.get("enabled"):
        await _begin_shortener_verification(client, message, token, shortener_settings, mode="per_link")
        return

    await _deliver_with_premium_gate(client, message, user_id, link_doc)


async def _handle_token_start(client, message: Message, token: str, settings: dict) -> None:
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name, user.last_name)
    await log_bot_start(client, user.id, user.username, user.first_name, has_token=True)

    if await db.is_muted(user.id):
        await message.reply_text(MUTED_USER_TEXT)
        return

    if not is_plausible_token(token):
        await message.reply_text(LINK_INVALID_TEXT)
        return

    link_doc = await db.get_link(token)
    if link_doc is None or link_doc.get("revoked"):
        await message.reply_text(LINK_INVALID_TEXT)
        return

    expires_at = link_doc.get("expires_at")
    if expires_at is not None and expires_at < time.time():
        await message.reply_text(LINK_EXPIRED_TEXT)
        return

    if settings.get("force_sub_enabled", True) and not await _premium_skips_force_sub(user.id):
        unsatisfied = await forcesub.get_unsatisfied_requirements(client, user.id)
        if unsatisfied:
            entries = await forcesub.get_display_entries()
            await _send_customizable_popup(
                client, message, settings.get("force_sub_message", {}), FORCE_SUB_TEXT,
                reply_markup=force_sub_user_keyboard(entries),
            )
            # remember which token they were trying to redeem so Verify can
            # resume it - stored in MongoDB (not the in-memory session) so
            # it survives the session timeout and host restarts.
            await db.set_pending_access(user.id, token)
            return

    await continue_after_force_sub(client, message, user.id, token, settings)


async def _handle_linkdl_start(client, message: Message, dl_token: str, settings: dict) -> None:
    """/start dl_<token> - the Force-Sub-gated form of a /linkdl link.
    The real browser download URL is only revealed once Force Sub is
    satisfied (or immediately, if Force Sub is off / nothing to join)."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name, user.last_name)
    await log_bot_start(client, user.id, user.username, user.first_name, has_token=True)

    if settings.get("force_sub_enabled", True) and not await _premium_skips_force_sub(user.id):
        unsatisfied = await forcesub.get_unsatisfied_requirements(client, user.id)
        if unsatisfied:
            entries = await forcesub.get_display_entries()
            await _send_customizable_popup(
                client, message, settings.get("force_sub_message", {}), FORCE_SUB_TEXT,
                reply_markup=force_sub_user_keyboard(entries),
            )
            await db.set_pending_access(user.id, LINKDL_START_PREFIX + dl_token)
            return

    await deliver_linkdl(client, message.chat.id, dl_token)


async def _handle_verification_start(client, message: Message, session_id: str, proof: str | None) -> None:
    """Resolves a `verify_<session_id><proof>` deep link — the shortener
    redirected here directly (see triss.services.shortener). A missing
    or incorrect `proof` is rejected outright regardless of timing;
    elapsed time alone is never treated as proof of completion."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name, user.last_name)

    if await db.is_muted(user.id):
        await message.reply_text(MUTED_USER_TEXT)
        return

    outcome, session = await shortener.evaluate_verification(session_id, proof)

    settings = await db.get_settings()
    shortener_settings = settings.get("shortener", {})
    # Use whichever settings sub-tree this SPECIFIC session was actually
    # created under (frozen on the session at creation time — see
    # triss.database.models.create_verification_session), not whatever
    # mode happens to be enabled right now. This keeps popups/anti-bypass
    # thresholds consistent even if the owner toggles modes mid-flow, and
    # is what decides whether a VERIFIED outcome below also grants a
    # System Access window or just delivers the one link (Old Method).
    session_mode = (session or {}).get("mode", "per_link")
    mode_settings = shortener_settings.get("system_access", {}) if session_mode == "system_access" else shortener_settings
    anti_bypass = mode_settings.get("anti_bypass", {})
    strike_limit = int(anti_bypass.get("strike_limit", 3))
    mute_seconds = int(anti_bypass.get("mute_seconds", 600))

    if outcome == shortener.VerificationOutcome.NOT_FOUND:
        await message.reply_text(SHORTENER_SESSION_INVALID_TEXT)
        return

    # A session belongs to exactly the user who created it — never act on
    # someone else's verification session even if they somehow obtain the id.
    if session is not None and session.get("user_id") != user.id:
        shortener.record_failed_attempt(user.id, window_seconds=mute_seconds)
        await message.reply_text(SHORTENER_SESSION_INVALID_TEXT)
        return

    if outcome == shortener.VerificationOutcome.INVALID_PROOF:
        shortener.record_failed_attempt(user.id, window_seconds=mute_seconds)
        await message.reply_text(SHORTENER_SESSION_INVALID_TEXT)
        return

    if outcome == shortener.VerificationOutcome.ALREADY_USED:
        await message.reply_text(SHORTENER_SESSION_INVALID_TEXT)
        return

    access_token = session["access_token"]

    if outcome == shortener.VerificationOutcome.BYPASS:
        # Strikes 1..(strike_limit-1) show bypass_message with a live
        # "Warning {count}" line and let the user retry immediately. The
        # strike that reaches strike_limit shows muted_message instead
        # (which never mentions a duration) - from then on, further
        # attempts are simply turned away by the is_rate_limited() check
        # above/in _begin_shortener_verification, until mute_seconds of
        # inactivity passes. This is a strike counter, not a ban: it
        # resets automatically once the window elapses or on a genuine
        # successful verification (see clear_failures() below).
        count = shortener.record_failed_attempt(user.id, window_seconds=mute_seconds)
        await log_bypass_detected(client, user.id, user.username, user.first_name, count, strike_limit)
        if count >= strike_limit:
            await _send_customizable_popup(
                client, message, mode_settings.get("muted_message", {}), SHORTENER_MUTED_TEXT,
            )
        else:
            bypass_config = mode_settings.get("bypass_message", {})
            base_text = bypass_config.get("text") or SHORTENER_BYPASS_TEXT
            text_with_count = f"{base_text}\n\n⚠️ Warning {count}/{strike_limit - 1}"
            await _send_customizable_popup(
                client, message, {**bypass_config, "text": text_with_count}, text_with_count,
                reply_markup=shortener_retry_keyboard(access_token, mode=session_mode),
            )
        return

    if outcome == shortener.VerificationOutcome.EXPIRED:
        shortener.record_failed_attempt(user.id, window_seconds=mute_seconds)
        await message.reply_text(SHORTENER_EXPIRED_TEXT,
                                  reply_markup=shortener_retry_keyboard(access_token, mode=session_mode))
        return

    # Force Sub must still hold at delivery time (the user may have left
    # a required chat while doing the shortener step). Checked BEFORE the
    # session is consumed so a lapsed user can rejoin and resume.
    if settings.get("force_sub_enabled", True) and not await _premium_skips_force_sub(user.id):
        unsatisfied = await forcesub.get_unsatisfied_requirements(client, user.id)
        if unsatisfied:
            entries = await forcesub.get_display_entries()
            await _send_customizable_popup(
                client, message, settings.get("force_sub_message", {}), FORCE_SUB_TEXT,
                reply_markup=force_sub_user_keyboard(entries),
            )
            # 🔓 rt_save has no real content link to resume via Verify
            # (it's a bare "please verify" session, not tied to a stored
            # link/batch) - so unlike the other modes, nothing is saved
            # to resume; the user just runs /rt_save again after joining.
            if session_mode != "rt_save":
                await db.set_pending_access(user.id, access_token)
            return

    # 🔓 rt_save (Restricted Content Save) - no content link involved at
    # all, so this short-circuits before the per_link/system_access logic
    # below, which all assume a real link_doc. See triss.handlers.start's
    # rt_save_cmd / capture_rt_save_link for the rest of the feature.
    if session_mode == "rt_save":
        consumed = await shortener.consume_session(session_id)
        if consumed is None:
            await message.reply_text(SHORTENER_SESSION_INVALID_TEXT)
            return
        shortener.clear_failures(user.id)
        rt_save_settings = settings.get("rt_save", {})
        duration_seconds = int(rt_save_settings.get("verify_duration_seconds", 3600))
        until = time.time() + duration_seconds
        await db.grant_rt_save_access(user.id, until)
        session_manager.set(user.id, "rt_save_awaiting_link")
        await message.reply_text(
            f"✅ Verified! You can use /rt_save freely for the next {format_duration(duration_seconds)}.\n\n"
            "Now send the restricted content's link (the t.me link you copied)."
        )
        return

    # VERIFIED so far — but do NOT consume the session yet. Validate the
    # underlying content link first (exists, not revoked, not expired) so
    # an invalid/revoked/expired link can never burn a verification
    # session for nothing; only consume once every condition needed for
    # actual delivery has already passed.
    link_doc = await db.get_link(access_token)
    if link_doc is None or link_doc.get("revoked"):
        await message.reply_text(LINK_INVALID_TEXT)
        return
    expires_at = link_doc.get("expires_at")
    if expires_at is not None and expires_at < time.time():
        await message.reply_text(LINK_EXPIRED_TEXT)
        return

    # All conditions satisfied — atomically consume the session immediately
    # before delivering anything, so a duplicate/concurrent /start update
    # for the same session can never trigger a second delivery (replay
    # protection). This remains the single point that gates delivery.
    consumed = await shortener.consume_session(session_id)
    if consumed is None:
        await message.reply_text(SHORTENER_SESSION_INVALID_TEXT)
        return

    shortener.clear_failures(user.id)
    verify_seconds = None
    created_at = consumed.get("created_at")
    completed_at = consumed.get("completed_at")
    if created_at is not None and completed_at is not None:
        verify_seconds = completed_at - created_at
    bot_username = getattr(client, "username", None) or (await client.get_me()).username
    delivered_link = build_deep_link(bot_username, access_token) if bot_username else None
    await log_verified(client, user.id, user.username, user.first_name, access_token,
                        verify_seconds=verify_seconds, delivered_link=delivered_link)

    if session_mode == "system_access":
        # New feature: one verification grants unlimited use of EVERY
        # link in the bot for `access_duration_seconds`, in addition to
        # delivering the specific link this verification was for. Force
        # Sub remains a separate gate and is never bypassed by this window.
        duration_seconds = int(mode_settings.get("access_duration_seconds", 21600))
        until = time.time() + duration_seconds
        await db.grant_system_access(user.id, until)
        try:
            await message.reply_text(
                f"♻️ System Access unlocked — you can use any link in this bot for the "
                f"next {format_duration(duration_seconds)} without verifying again."
            )
        except RPCError:
            pass

    delivered = await deliver_and_schedule(client, user.id, link_doc)
    if not delivered:
        await message.reply_text("⚠️ Sorry, this content could not be delivered right now.")


@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message: Message) -> None:
    settings = await db.get_settings()

    if await db.is_banned(message.from_user.id):
        await message.reply_text(BANNED_USER_TEXT)
        return

    # Maintenance Mode is enforced globally, before this handler can even
    # run — see triss.handlers.maintenance_gate.

    args = message.command
    payload = args[1].strip() if len(args) > 1 and args[1].strip() else None

    if payload is None:
        await _handle_plain_start(message, settings)
    elif payload.startswith(VERIFY_PREFIX):
        session_id, proof = shortener.parse_verify_payload(payload[len(VERIFY_PREFIX):])
        if session_id is None:
            await message.reply_text(SHORTENER_SESSION_INVALID_TEXT)
            return
        await _handle_verification_start(client, message, session_id, proof)
    elif payload.startswith(LINKDL_START_PREFIX) and len(payload) > len(LINKDL_START_PREFIX):
        await _handle_linkdl_start(client, message, payload[len(LINKDL_START_PREFIX):], settings)
    else:
        await _handle_token_start(client, message, payload, settings)
  


# ---------------------------------------------------------------------------
# 🔓 /rt_save (Restrict Content Save)
# ---------------------------------------------------------------------------

_RT_SAVE_EXCLUDED_COMMANDS = [
    "genlink", "batch", "done", "cancelbatch", "autobatch", "broadcast", "settings", "start",
    "addadmin", "removeadmin", "listadmin", "mute", "unmute", "listmute",
    "ban", "unban", "listban", "linkdl", "setlinkcaption", "buy_premium",
    "setvip", "listvip", "deletevip", "search", "rt_save", "help",
]

_RT_SAVE_PRIVATE_LINK_RE = re.compile(r"t\.me/c/(\d+)/(?:\d+/)?(\d+)")
_RT_SAVE_PUBLIC_LINK_RE = re.compile(r"t\.me/([A-Za-z][A-Za-z0-9_]{3,})/(\d+)")


def _parse_rt_save_link(text: str):
    """(chat_ref, message_id) from a t.me post link, private (/c/<internal
    id>) or public (/<username>), or None if no link is found."""
    m = _RT_SAVE_PRIVATE_LINK_RE.search(text)
    if m:
        return int("-100" + m.group(1)), int(m.group(2))
    m = _RT_SAVE_PUBLIC_LINK_RE.search(text)
    if m:
        return m.group(1), int(m.group(2))
    return None


@app.on_message(filters.command("rt_save") & filters.private)
async def rt_save_cmd(client, message: Message) -> None:
    user_id = message.from_user.id
    settings = await db.get_settings()
    rt_save_settings = settings.get("rt_save", {})
    if not rt_save_settings.get("enabled"):
        await message.reply_text("⚠️ This feature is currently disabled.")
        return

    if await db.has_active_rt_save_access(user_id):
        session_manager.set(user_id, "rt_save_awaiting_link")
        await message.reply_text("🔓 Send the restricted content's link (the t.me link you copied).")
        return

    shortener_settings = settings.get("shortener", {})
    await _begin_shortener_verification(client, message, "", shortener_settings, mode="rt_save")


@app.on_message(filters.private & session_is("rt_save_awaiting_link")
                 & filters.text & ~filters.command(_RT_SAVE_EXCLUDED_COMMANDS))
async def capture_rt_save_link(client, message: Message) -> None:
    user_id = message.from_user.id
    if not await db.has_active_rt_save_access(user_id):
        session_manager.clear(user_id)
        await message.reply_text("⚠️ Your verified window expired - run /rt_save again.")
        return

    ref = _parse_rt_save_link(message.text or "")
    if ref is None:
        await message.reply_text("⚠️ That doesn't look like a t.me post link. Send the link again.")
        return
    chat_ref, source_message_id = ref

    try:
        source = await client.get_messages(chat_ref, source_message_id)
    except Exception:
        source = None
    if source is None or source.empty:
        await message.reply_text(
            "⚠️ Couldn't read that message. Make sure I'm an admin in that chat, then try again."
        )
        return

    try:
        await client.copy_message(chat_id=user_id, from_chat_id=source.chat.id, message_id=source.id)
    except Exception:
        logger.exception("rt_save: failed to deliver unlocked content to user %s.", user_id)
        await message.reply_text("⚠️ Couldn't unlock that message. Check that I'm an admin there.")
        return

    log_channel_id = await db.get_active_channel_id("log")
    if log_channel_id:
        try:
            await client.copy_message(chat_id=log_channel_id, from_chat_id=source.chat.id, message_id=source.id)
        except Exception:
            logger.warning("rt_save: could not copy to Log Channel.", exc_info=True)

    await message.reply_text("✅ Here it is — send another link, or just stop whenever you're done.")

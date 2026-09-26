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
import time

from pyrogram import filters
from pyrogram.types import Message, LinkPreviewOptions
from pyrogram.errors import RPCError

from triss.bot import app
from triss.database import models as db
from triss.services import forcesub, shortener
from triss.services.cleanup import session_manager
from triss.services.delivery import deliver_and_schedule, schedule_auto_delete
from triss.services.logging_service import log_bot_start, log_verified, log_bypass_detected
from triss.utils.auth import is_owner
from triss.utils.formatting import (
    DEFAULT_WELCOME_TEXT,
    MAINTENANCE_TEXT,
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
)
from triss.utils.tokens import is_plausible_token
from triss.utils.effects import pick_category_effect_id, call_with_optional_effect

logger = logging.getLogger("triss.handlers.start")

_SPEED_DELAYS = {
    "slow": (1.6, 1.6, 1.6),
    "default": (0.9, 0.9, 0.9),
    "speed": (0.35, 0.35, 0.35),
}

VERIFY_PREFIX = "verify_"


async def send_welcome_media(message: Message, welcome: dict, text: str,
                              message_effect_id: int | None = None) -> Message:
    """Item 8: welcome media was photo-only before — now sends whichever
    of photo/video/animation(gif) is configured via welcome.media_type
    (default "photo", so every pre-existing configured welcome photo
    keeps working unchanged). Shared by the real /start flow here and by
    triss.handlers.callbacks' welcome:preview action, so both stay in
    sync automatically.

    Uses app.send_* (Client-level) rather than message.reply_* (the
    bound-method shortcuts) on purpose: this installed Pyrogram/Kurigram
    build's reply_*/reply shortcuts don't forward message_effect_id
    through to their underlying send_* target (confirmed via Render logs
    — TypeError: unexpected keyword argument 'message_effect_id' on
    reply_photo/reply/reply_video/reply_animation specifically), while
    the Client-level send_* methods they wrap DO accept it correctly.
    Same fix applied to _send_customizable_popup below and to linkdl.py's
    final caption reply."""
    chat_id = message.chat.id
    media_type = welcome.get("media_type", "photo")
    spoiler = bool(welcome.get("spoiler"))
    if media_type == "video" and welcome.get("video_file_id"):
        return await call_with_optional_effect(
            app.send_video, chat_id=chat_id, video=welcome["video_file_id"], caption=text,
            has_spoiler=spoiler, message_effect_id=message_effect_id,
        )
    if media_type == "animation" and welcome.get("animation_file_id"):
        return await call_with_optional_effect(
            app.send_animation, chat_id=chat_id, animation=welcome["animation_file_id"], caption=text,
            has_spoiler=spoiler, message_effect_id=message_effect_id,
        )
    if media_type == "photo" and welcome.get("photo_file_id"):
        return await call_with_optional_effect(
            app.send_photo, chat_id=chat_id, photo=welcome["photo_file_id"], caption=text,
            has_spoiler=spoiler, message_effect_id=message_effect_id,
        )
    return await call_with_optional_effect(
        app.send_message, chat_id=chat_id, text=text,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
        message_effect_id=message_effect_id,
    )


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
    start_effect_id = pick_category_effect_id(settings, "start")

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

    sent = await send_welcome_media(message, welcome, text, message_effect_id=start_effect_id)
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
                                    fallback_text: str, reply_markup=None,
                                    message_effect_id: int | None = None) -> None:
    """Shared sender for the three owner-customizable shortener popups
    (verify_message / bypass_message / muted_message - see
    triss.database.mongodb DEFAULT_SETTINGS and triss.handlers.callbacks'
    shortener:{verifymsg,bypassmsg,mutedmsg} submenus). `popup_config` is
    one of those dicts: {"text", "photo_file_id", "spoiler"}. An empty/
    missing text falls back to `fallback_text` (the hardcoded default in
    triss.utils.formatting)."""
    popup_config = popup_config or {}
    text = popup_config.get("text") or fallback_text
    photo_id = popup_config.get("photo_file_id")
    if photo_id:
        await call_with_optional_effect(
            client.send_photo,
            chat_id=message.chat.id, photo=photo_id, caption=text,
            has_spoiler=bool(popup_config.get("spoiler")),
            reply_markup=reply_markup,
            message_effect_id=message_effect_id,
        )
    else:
        await call_with_optional_effect(
            client.send_message,
            chat_id=message.chat.id, text=text, reply_markup=reply_markup,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            message_effect_id=message_effect_id,
        )


async def _begin_shortener_verification(client, message: Message, token: str, shortener_settings: dict) -> None:
    user_id = message.from_user.id
    anti_bypass = shortener_settings.get("anti_bypass", {})
    strike_limit = int(anti_bypass.get("strike_limit", 3))
    mute_seconds = int(anti_bypass.get("mute_seconds", 600))

    if shortener.is_rate_limited(user_id, threshold=strike_limit, window_seconds=mute_seconds):
        await message.reply_text(SHORTENER_RATE_LIMITED_TEXT)
        return

    username = await _bot_username(client)
    session, short_url = await shortener.start_new_verification(username, user_id, token, shortener_settings)

    if short_url is None:
        logger.error("Could not generate a shortener link for user %s (token=%s).", user_id, token)
        await message.reply_text(SHORTENER_UNAVAILABLE_TEXT)
        return

    tutorial_url = shortener_settings.get("tutorial_url")
    await _send_customizable_popup(
        client, message, shortener_settings.get("verify_message", {}), SHORTENER_VERIFY_TEXT,
        reply_markup=shortener_verification_keyboard(short_url, tutorial_url),
    )


async def continue_after_force_sub(client, message: Message, user_id: int, token: str, settings: dict) -> None:
    """Shared continuation used both by the normal token flow (once Force
    Sub is already satisfied) and by the Force-Sub 'Verify' button (once
    Force Sub becomes satisfied) — so a resumed access goes through
    Shortener verification exactly like a fresh one, instead of skipping it."""
    link_doc = await db.get_link(token)
    if link_doc is None or link_doc.get("revoked"):
        await message.reply_text(LINK_INVALID_TEXT)
        return
    expires_at = link_doc.get("expires_at")
    if expires_at is not None and expires_at < time.time():
        await message.reply_text(LINK_EXPIRED_TEXT)
        return

    shortener_settings = settings.get("shortener", {})
    if shortener_settings.get("enabled"):
        await _begin_shortener_verification(client, message, token, shortener_settings)
        return

    delivered = await deliver_and_schedule(client, user_id, link_doc)
    if not delivered:
        await message.reply_text("⚠️ Sorry, this content could not be delivered right now.")


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

    if settings.get("force_sub_enabled", True):
        unsatisfied = await forcesub.get_unsatisfied_requirements(client, user.id)
        if unsatisfied:
            entries = await forcesub.get_display_entries()
            await _send_customizable_popup(
                client, message, settings.get("force_sub_message", {}), FORCE_SUB_TEXT,
                reply_markup=force_sub_user_keyboard(entries),
            )
            # remember which token they were trying to redeem so Verify can resume it
            session_manager.set(user.id, "forcesub_pending_token", {"token": token})
            return

    await continue_after_force_sub(client, message, user.id, token, settings)


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
    anti_bypass = shortener_settings.get("anti_bypass", {})
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
                client, message, shortener_settings.get("muted_message", {}), SHORTENER_MUTED_TEXT,
            )
        else:
            bypass_config = shortener_settings.get("bypass_message", {})
            base_text = bypass_config.get("text") or SHORTENER_BYPASS_TEXT
            text_with_count = f"{base_text}\n\n⚠️ Warning {count}/{strike_limit - 1}"
            effect_id = pick_category_effect_id(settings, "bypass")
            await _send_customizable_popup(
                client, message, {**bypass_config, "text": text_with_count}, text_with_count,
                reply_markup=shortener_retry_keyboard(access_token),
                message_effect_id=effect_id,
            )
        return

    if outcome == shortener.VerificationOutcome.EXPIRED:
        shortener.record_failed_attempt(user.id, window_seconds=mute_seconds)
        await message.reply_text(SHORTENER_EXPIRED_TEXT, reply_markup=shortener_retry_keyboard(access_token))
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
    await log_verified(client, user.id, user.username, user.first_name, access_token)

    delivered = await deliver_and_schedule(client, user.id, link_doc)
    if not delivered:
        await message.reply_text("⚠️ Sorry, this content could not be delivered right now.")


@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message: Message) -> None:
    settings = await db.get_settings()

    if await db.is_banned(message.from_user.id):
        await message.reply_text(BANNED_USER_TEXT)
        return

    if settings.get("maintenance") and not is_owner(message.from_user.id):
        await message.reply_text(MAINTENANCE_TEXT)
        return

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
    else:
        await _handle_token_start(client, message, payload, settings)
  

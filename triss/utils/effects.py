"""
triss.utils.effects
=====================
Item 7 — Telegram's built-in animated "message effects" (the full-screen
🎉/🔥/❤️ animation that plays when a message is delivered in a private
chat — pyrogram/kurigram's `message_effect_id` parameter on send_*/copy_
message, Bot API 7.2+). These are Telegram-side animations, not
something the bot draws itself — the bot only needs to pass the right
numeric id.

IMPORTANT: effects only render in ONE-ON-ONE PRIVATE CHATS (Telegram
API restriction, not a bot limitation) — exactly where file delivery in
this bot always happens, so no gating is needed for that reason.

These 6 are the standard, always-available effects (no Telegram Premium
required on the RECEIVING end to see them). IDs are stable Telegram-side
constants, not something this bot invents.
"""

from __future__ import annotations

MESSAGE_EFFECTS: dict[str, int] = {
    "fire": 5104841245755180586,       # 🔥
    "party": 5046509860389126442,      # 🎉
    "heart": 5159385139981059251,      # ❤️
    "thumbsup": 5107584321108051014,   # 👍
    "thumbsdown": 5104858069142078462,  # 👎
    "poop": 5046589136895476101,       # 💩
}

EFFECT_LABELS: dict[str, str] = {
    "fire": "🔥 Fire",
    "party": "🎉 Party Popper",
    "heart": "❤️ Heart",
    "thumbsup": "👍 Thumbs Up",
    "thumbsdown": "👎 Thumbs Down",
    "poop": "💩 Poop",
}


def resolve_effect_id(name: str | None) -> int | None:
    if not name:
        return None
    return MESSAGE_EFFECTS.get(name)


async def call_with_optional_effect(func, **kwargs):
    """Calls a Pyrogram send_*/reply_* coroutine, including
    message_effect_id only when useful, and retrying once without it if
    the installed Pyrogram/Kurigram build rejects the kwarg outright
    (some builds only support message_effect_id on a subset of send
    methods - see triss.services.delivery for the same issue on
    copy_message, which never supports it at all per the Bot API)."""
    try:
        return await func(**kwargs)
    except TypeError as e:
        if "message_effect_id" in kwargs and "message_effect_id" in str(e):
            kwargs = {k: v for k, v in kwargs.items() if k != "message_effect_id"}
            return await func(**kwargs)
        raise
        

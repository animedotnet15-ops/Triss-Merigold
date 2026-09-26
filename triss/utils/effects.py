"""
triss.utils.effects
=====================
Telegram's built-in animated "message effects" (the full-screen emoji
animation that plays when a message is delivered in a private chat —
pyrogram/kurigram's `message_effect_id` parameter on send_*/copy_message,
Bot API 7.2+). These are Telegram-side animations rendered by the
Telegram client itself, not something the bot draws — the bot only needs
to pass the right numeric id.

IMPORTANT: effects only render in ONE-ON-ONE PRIVATE CHATS (Telegram API
restriction, not a bot limitation) — exactly where file delivery in this
bot always happens, so no gating is needed for that reason.

--------------------------------------------------------------------------
EXACT EFFECT SET (per spec) AND WHERE THE IDS COME FROM
--------------------------------------------------------------------------
🕊️ 🎊 🎉 🔥 ⚡ 💐 ✨ 🤣 🖕 — these 9 ARE real, currently-available Telegram
message effects (Telegram's effect catalog is much larger than the small
"6 standard effects" set from Bot API 7.2's initial launch — it has grown
substantially since). MESSAGE_EFFECTS below seeds each with its known-
correct numeric id.

Per spec ("use the correct Telegram Bot API/Pyrogram mechanism"), the
authoritative source is Client.get_message_effects() — a real Pyrogram/
Kurigram method that fetches Telegram's live effect catalog. `refresh_
message_effects()` calls it once at startup (see triss.bot.startup) and
overwrites any id above whose live value has changed, so this file's
hardcoded ids are a correct-today SEED / offline fallback, never the
sole source of truth. If get_message_effects() isn't available on the
installed library version, or the call fails for any reason, the seed
ids above are used as-is — this bot degrades gracefully either way,
never crashes on it.
"""

from __future__ import annotations

import logging
import random
import unicodedata

logger = logging.getLogger("triss.effects")

# Seed ids — refreshed from the live Telegram catalog at startup when
# possible (see refresh_message_effects below), but immediately usable
# even if that refresh doesn't happen (e.g. older Kurigram build).
MESSAGE_EFFECTS: dict[str, int] = {
    "dove": 5095856784057303751,          # 🕊️
    "confetti": 5041819580008236993,      # 🎊
    "party": 5046509860389126442,         # 🎉
    "fire": 5104841245755180586,          # 🔥
    "lightning": 5123236135417415011,     # ⚡
    "bouquet": 5026421185949205937,       # 💐
    "sparkles": 5089460564141278042,      # ✨
    "rofl": 5066993302453093673,          # 🤣
    "middlefinger": 4961092903720977544,  # 🖕
}

EFFECT_LABELS: dict[str, str] = {
    "dove": "🕊️ Dove",
    "confetti": "🎊 Confetti Ball",
    "party": "🎉 Party Popper",
    "fire": "🔥 Fire",
    "lightning": "⚡ Lightning",
    "bouquet": "💐 Bouquet",
    "sparkles": "✨ Sparkles",
    "rofl": "🤣 Rofl",
    "middlefinger": "🖕 Middle Finger",
}

# The literal emoji each key represents - used only to match against
# Client.get_message_effects()'s live results in refresh_message_effects.
_EFFECT_EMOJI: dict[str, str] = {
    "dove": "🕊",
    "confetti": "🎊",
    "party": "🎉",
    "fire": "🔥",
    "lightning": "⚡",
    "bouquet": "💐",
    "sparkles": "✨",
    "rofl": "🤣",
    "middlefinger": "🖕",
}

# The 4 places in the bot that can play an effect - one settings category
# each, all living together under settings.effects (see
# triss.database.mongodb DEFAULT_SETTINGS).
EFFECT_CATEGORIES: dict[str, str] = {
    "start": "⚡ Start Effect",
    "bypass": "🚨 Bypass Detected Effect",
    "delivery": "🕊️ Files Delivery Effect",
    "linkdl": "🔥 Direct Download Effect",
}


def _normalize_emoji(emoji: str) -> str:
    """Strips variation selectors (U+FE0E/U+FE0F) so "🕊️" (with VS16) and
    "🕊" (bare) compare equal - Telegram's live catalog and this bot's
    own emoji literals aren't guaranteed to use the same form."""
    return "".join(ch for ch in emoji if unicodedata.category(ch) != "Mn" and ch not in ("\ufe0e", "\ufe0f"))


async def refresh_message_effects(client) -> None:
    """Calls Client.get_message_effects() (the correct, current Pyrogram/
    Kurigram mechanism for this - see module docstring) and updates
    MESSAGE_EFFECTS in place for any of our 9 emojis whose live id
    differs from the hardcoded seed above. Call once at startup
    (triss.bot.startup) - safe to skip/fail silently since the seed ids
    already work standalone."""
    get_effects = getattr(client, "get_message_effects", None)
    if get_effects is None:
        logger.info("This Pyrogram/Kurigram build has no get_message_effects() - using seed effect ids as-is.")
        return
    try:
        live_effects = await get_effects()
    except Exception:
        logger.warning("Could not fetch live message effects from Telegram - using seed effect ids.", exc_info=True)
        return

    target_by_emoji = {_normalize_emoji(e): key for key, e in _EFFECT_EMOJI.items()}
    matched: set[str] = set()
    for effect in live_effects:
        key = target_by_emoji.get(_normalize_emoji(getattr(effect, "emoji", "") or ""))
        if key is None:
            continue
        # Prefer a non-premium match if we see one later for the same
        # emoji; keep the first match otherwise. Multiple ids can share
        # one emoji (see the live catalog) - any valid one works.
        if key not in matched or not getattr(effect, "is_premium", False):
            MESSAGE_EFFECTS[key] = effect.id
            matched.add(key)

    missing = set(_EFFECT_EMOJI) - matched
    if missing:
        logger.info(
            "Telegram's live effect catalog didn't include %s - keeping this bot's seed id(s) for them "
            "(unconfirmed - if effects using these don't animate, this is why).",
            ", ".join(EFFECT_LABELS[k] for k in missing),
        )
    logger.info(
        "Message effects ready: %d/%d confirmed live (%s), %d unconfirmed seed (%s).",
        len(matched), len(_EFFECT_EMOJI), ", ".join(sorted(matched)) or "none",
        len(missing), ", ".join(sorted(missing)) or "none",
    )


def resolve_effect_id(name: str | None) -> int | None:
    if not name:
        return None
    return MESSAGE_EFFECTS.get(name)


def pick_category_effect_id(settings: dict, category: str) -> int | None:
    """Resolves the effect id to use for one category (start/bypass/
    delivery/linkdl), honouring the master on/off switch, the
    per-category on/off switch, and - since multiple emojis can be
    selected per category - picking one at random from whichever are
    currently enabled for it. Telegram only allows ONE effect per
    message, so a random pick is how "multiple emojis enabled" turns
    into an actual send."""
    effects_settings = settings.get("effects", {})
    if not effects_settings.get("master_enabled", True):
        return None
    cat = effects_settings.get("categories", {}).get(category, {})
    if not cat.get("enabled"):
        return None
    selected = cat.get("selected") or []
    if not selected:
        return None
    return resolve_effect_id(random.choice(selected))


async def call_with_optional_effect(func, **kwargs):
    """Calls a Pyrogram send_*/reply_* coroutine, including
    message_effect_id only when useful, and retrying once without it if
    it can't go through - either the installed Pyrogram/Kurigram build
    rejects the kwarg outright (TypeError - some builds only support
    message_effect_id on a subset of send methods, see
    triss.services.delivery for the same issue on copy_message, which
    never supports it at all per the Bot API), or Telegram itself
    rejects the id at send time (an RPCError - e.g. a seed id that live
    catalog refresh didn't confirm turning out to be wrong). Both cases
    are logged loudly instead of swallowed, so a "no animation" report
    can be root-caused from the logs instead of guessed at."""
    effect_id = kwargs.get("message_effect_id")
    if effect_id is not None:
        logger.info("Sending with message_effect_id=%s via %s", effect_id, getattr(func, "__name__", func))
    try:
        return await func(**kwargs)
    except Exception as e:
        if effect_id is None or "message_effect_id" not in kwargs:
            raise
        logger.warning(
            "message_effect_id=%s was rejected by %s (%s: %s) - retrying without it.",
            effect_id, getattr(func, "__name__", func), type(e).__name__, e,
        )
        kwargs = {k: v for k, v in kwargs.items() if k != "message_effect_id"}
        return await func(**kwargs)

    

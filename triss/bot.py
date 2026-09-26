"""
triss.bot
=========
Owns the single Kurigram `Client` instance. Handler modules are imported
at the bottom of this file (after `app` exists) purely for their
side-effect of registering `@app.on_message` / `@app.on_callback_query`
decorators — this avoids circular imports while keeping each handler
file self-contained.
"""

from __future__ import annotations

import asyncio
import logging

from pyrogram import Client
from pyrogram.enums import ParseMode

from triss.config import config
from triss.database.mongodb import database
from triss.services.cleanup import periodic_cleanup_loop

logger = logging.getLogger("triss.bot")

app = Client(
    name=config.session_name,
    api_id=config.api_id,
    api_hash=config.api_hash,
    bot_token=config.bot_token,
    in_memory=True,
    # BUG FIX (font/symbol support): Pyrogram's default (no parse_mode
    # set) mixes Markdown AND HTML syntax in the same message, but the
    # Markdown dialect this bot's templates used to rely on (see
    # triss/utils/formatting.py) has 9 reserved characters (\*_~`|[]()) -
    # any of them appearing LITERALLY in owner-typed custom text (a
    # pasted "fancy font"/symbol decoration, a stray asterisk, etc.) gets
    # misread as a formatting delimiter, corrupting or truncating the
    # message. HTML has only 3 reserved characters (& < >), which
    # triss.utils.formatting now escapes correctly wherever dynamic text
    # is substituted - pinning parse_mode to HTML here, with every
    # template converted to <b>/<code> tags, removes that class of bug.
    parse_mode=ParseMode.HTML,
)

_cleanup_task: asyncio.Task | None = None


async def startup() -> None:
    logger.info("Starting Triss File Store Bot...")
    try:
        import importlib.metadata
        logger.info("kurigram package version installed: %s", importlib.metadata.version("kurigram"))
    except Exception:
        logger.info("Could not determine installed kurigram package version.")
    await database.connect()

    # Warm the in-memory admin cache (see triss.utils.auth) so every
    # owner-only permission check works immediately, before the first
    # /addadmin/removeadmin call of this run.
    from triss.database import models as db
    from triss.utils import auth
    auth.set_admin_cache(await db.get_admin_ids())

    await app.start()
    me = await app.get_me()
    app.username = me.username  # convenient cache used by deep-link builders

    # Refresh MESSAGE_EFFECTS from Telegram's live catalog (falls back to
    # the hardcoded seed ids on any failure - see triss.utils.effects).
    from triss.utils.effects import refresh_message_effects
    await refresh_message_effects(app)

    global _cleanup_task
    _cleanup_task = asyncio.create_task(periodic_cleanup_loop())
    logger.info("Bot started as @%s (id=%s).", me.username, me.id)

    await _verify_storage_channel_access()


async def _verify_storage_channel_access() -> None:
    """Catches a broken Store Channel (bot removed/demoted, channel
    recreated with a stale ID in config/DB, etc.) at boot instead of at
    the first /genlink, /batch, or delivery attempt. This is exactly the
    failure mode where the bot shows 'Live' and connects fine, then every
    genlink/batch/old-link silently fails deep inside
    storage.py/delivery.py - surfacing it here, loudly, in logs (and to
    the owner) removes the guesswork.
    """
    from triss.database import models as db
    from pyrogram.errors import RPCError
    from pyrogram.enums import ChatMemberStatus

    try:
        settings = await db.get_settings()
        channel_id = settings.get("storage_channel_id") or config.storage_channel_id
        if not channel_id:
            logger.warning(
                "No Store Channel configured yet - /genlink and /batch will "
                "refuse to store content until one is set via /settings -> "
                "Store Channel."
            )
            return

        member = await app.get_chat_member(channel_id, "me")
        if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            raise RuntimeError(f"bot is only '{member.status}' in the Store Channel, not admin")

        logger.info("Store Channel access verified (id=%s).", channel_id)
    except Exception as e:
        logger.critical(
            "STORE CHANNEL CHECK FAILED (id=%s): %s. genlink, batch, and "
            "delivery of every existing link will fail until this is fixed - "
            "re-add the bot as admin in that channel (Post Messages + Edit "
            "Messages permissions), or update the Store Channel via "
            "/settings if the channel itself changed.",
            channel_id, e,
        )
        try:
            await app.send_message(
                config.owner_id,
                "🚨 Store Channel check failed at startup: "
                f"<code>{e}</code>\n\n"
                "genlink/batch/link delivery will not work until the bot is "
                "re-added as admin in the Store Channel, or you reconfigure "
                "it via /settings -> 🏪 Store Channel.",
            )
        except RPCError:
            pass


async def shutdown() -> None:
    logger.info("Shutting down Triss File Store Bot...")
    if _cleanup_task is not None:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass
    try:
        await app.stop()
    except Exception:
        logger.exception("Error while stopping the Telegram client.")
    await database.close()
    logger.info("Shutdown complete.")


# Import handler modules for their registration side effects. Order does
# not matter for filter dispatch (Pyrogram routes by filter, not import
# order) but is kept roughly command-then-callback for readability.
from triss.handlers import start as _start_handlers  # noqa: E402,F401
from triss.handlers import genlink as _genlink_handlers  # noqa: E402,F401
from triss.handlers import batch as _batch_handlers  # noqa: E402,F401
from triss.handlers import broadcast as _broadcast_handlers  # noqa: E402,F401
from triss.handlers import settings as _settings_handlers  # noqa: E402,F401
from triss.handlers import callbacks as _callback_handlers  # noqa: E402,F401
from triss.handlers import admin as _admin_handlers  # noqa: E402,F401
from triss.handlers import linkdl as _linkdl_handlers  # noqa: E402,F401
from triss.handlers import help as _help_handlers  # noqa: E402,F401

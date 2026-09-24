"""
triss.database.mongodb
=======================
Owns the single AsyncIOMotorClient instance for the process, exposes
typed collection accessors, and creates indexes on startup.

Collections:
    users                  -> one document per Telegram user who has /start'ed the bot
    settings               -> a single document holding all bot configuration
                               (welcome, force sub toggle, auto delete, maintenance,
                               store channel, and shortener configuration)
    links                  -> one document per generated share link (genlink/batch)
    force_subs             -> force-subscription entries (channel/group/folder)
    backups                -> stored configuration/metadata backups
    broadcast_jobs          -> lightweight record of the most recent broadcast run
    verification_sessions  -> one document per PER-USER, PER-ACCESS shortener
                               verification attempt (never reused across accesses
                               or across users — see triss.services.shortener)

The 'settings' collection intentionally holds a single document with a
fixed _id so it can be fetched/updated with simple point queries and
atomic $set operations (no race condition between concurrent settings edits
because MongoDB applies a single update document atomically). Shortener
configuration is stored as a nested `shortener` object inside this same
document rather than a separate collection, reusing the existing
settings read/update path instead of introducing a parallel one.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase, AsyncIOMotorCollection
from pymongo.errors import PyMongoError

from triss.config import config

logger = logging.getLogger("triss.database")

SETTINGS_DOC_ID = "bot_settings"

DEFAULT_SETTINGS: dict[str, Any] = {
    "_id": SETTINGS_DOC_ID,
    "welcome": {
        "photo_file_id": None,
        "video_file_id": None,
        "animation_file_id": None,
        # Which of the three above to actually send - "photo" | "video" |
        # "animation". Item 8: welcome media was photo-only before;
        # defaulting to "photo" keeps every existing configured welcome
        # photo working unchanged after this upgrade (see
        # triss.handlers.start._send_welcome).
        "media_type": "photo",
        "text": None,  # None -> use DEFAULT_WELCOME_TEXT from utils.formatting
        "spoiler": False,
        "sticker_file_id": None,
        "sticker_enabled": False,
        "animation_speed": "default",  # slow | default | speed
    },
    "force_sub_enabled": True,
    # Item 6: /linkdl - real browser-downloadable HTTP links (unlike the
    # rest of this bot, which always delivers via Telegram deep links).
    # STATELESS by design (no per-link database record - see
    # triss.utils.linkdl_token): the file lives only in LOG_CHANNEL_ID,
    # and the token is a signed reference to it, not a lookup key.
    # "enabled" is the "🌍 Public Use" toggle (/settings -> Direct
    # Download Links) - it's the ONLY way to disable a linkdl link, since
    # there's no individual record to revoke. While off, sending files
    # does nothing extra AND every existing /dl/<token> URL is rejected
    # (see triss.web.server).
    # "caption" supports a {link} placeholder; None -> DEFAULT_LINKDL_CAPTION.
    "linkdl": {"enabled": False, "caption": None},
    # Item 7: Telegram's built-in animated message effects (🎉/🔥/etc,
    # see triss.utils.effects) played on the final delivered file
    # message - purely cosmetic, private-chat-only Telegram feature.
    "delivery_effect": {"enabled": False, "effect": "fire"},
    # Item 2: customizable Force Sub prompt (text + optional photo/spoiler),
    # shown instead of the default FORCE_SUB_TEXT wherever a user hasn't
    # joined the required channels/groups yet. Per-entry custom button
    # labels live on the force_sub entry itself (see add_force_sub's
    # button_text param), not here.
    "force_sub_message": {"text": None, "photo_file_id": None, "spoiler": False},
    "auto_delete": {
        "enabled": False,
        "seconds": 0,
        # When False, the "this will be auto-deleted" notice message is
        # skipped entirely - auto-delete itself (the scheduled deletion)
        # still runs exactly the same either way. See
        # triss/services/delivery.py schedule_auto_delete().
        "notify": True,
    },
    "maintenance": False,
    "storage_channel_id": config.storage_channel_id,
    "link_expiry_seconds": None,
    "shortener": {
        "enabled": False,
        "domain": None,
        "api_key": None,
        "minimum_seconds": 150,
        "maximum_seconds": 500,
        "tutorial_url": None,
        # Customizable "please verify" popup - text/photo/spoiler. `text`
        # None falls back to SHORTENER_VERIFY_TEXT (triss/utils/formatting.py).
        "verify_message": {"text": None, "photo_file_id": None, "spoiler": False},
        # Customizable "bypass detected" popup shown on strikes 1..N-1.
        # `text` None falls back to SHORTENER_BYPASS_TEXT. The live "Warning
        # {count}" line is appended automatically - do not put a literal
        # {count} in here unless you want it to appear twice.
        "bypass_message": {"text": None, "photo_file_id": None, "spoiler": False},
        # Shown once instead of bypass_message on the strike that reaches
        # strike_limit (see anti_bypass below). Deliberately never mentions
        # how long the mute lasts. `text` None falls back to
        # SHORTENER_MUTED_TEXT.
        "muted_message": {"text": None, "photo_file_id": None, "spoiler": False},
        "anti_bypass": {
            # Number of bypass attempts (within mute_seconds of each other)
            # before the user is temporarily muted from starting new
            # verification sessions. Attempts 1..(strike_limit-1) show
            # bypass_message with a "Warning N" count; the strike_limit-th
            # attempt shows muted_message instead and the user is then
            # blocked (SHORTENER_RATE_LIMITED_TEXT) until mute_seconds
            # of inactivity passes - see triss/services/shortener.py.
            "strike_limit": 3,
            "mute_seconds": 600,
        },
    },
}


class Database:
    def __init__(self) -> None:
        self.client: Optional[AsyncIOMotorClient] = None
        self.db: Optional[AsyncIOMotorDatabase] = None

    async def connect(self) -> None:
        logger.info("Connecting to MongoDB (db=%s)...", config.database_name)
        self.client = AsyncIOMotorClient(config.mongo_uri, serverSelectionTimeoutMS=8000)
        self.db = self.client[config.database_name]
        try:
            await self.client.admin.command("ping")
        except PyMongoError:
            logger.critical("Could not reach MongoDB. Check MONGO_URI.")
            raise
        logger.info("MongoDB connection established.")
        await self._ensure_indexes()
        await self._ensure_default_settings()

    async def close(self) -> None:
        if self.client is not None:
            self.client.close()
            logger.info("MongoDB connection closed.")

    # -- collection accessors -------------------------------------------------

    @property
    def users(self) -> AsyncIOMotorCollection:
        return self.db["users"]

    @property
    def settings(self) -> AsyncIOMotorCollection:
        return self.db["settings"]

    @property
    def links(self) -> AsyncIOMotorCollection:
        return self.db["links"]

    @property
    def force_subs(self) -> AsyncIOMotorCollection:
        return self.db["force_subs"]

    @property
    def admins(self) -> AsyncIOMotorCollection:
        return self.db["admins"]

    @property
    def mutes(self) -> AsyncIOMotorCollection:
        return self.db["mutes"]

    @property
    def backups(self) -> AsyncIOMotorCollection:
        return self.db["backups"]

    @property
    def broadcast_jobs(self) -> AsyncIOMotorCollection:
        return self.db["broadcast_jobs"]

    @property
    def verification_sessions(self) -> AsyncIOMotorCollection:
        return self.db["verification_sessions"]

    # -- setup ------------------------------------------------------------

    async def _ensure_indexes(self) -> None:
        try:
            await self.users.create_index("user_id", unique=True)
            await self.users.create_index("joined_at")

            await self.links.create_index("token", unique=True)
            await self.links.create_index("created_at")
            await self.links.create_index("expires_at")
            await self.links.create_index("batch_id")

            await self.force_subs.create_index([("kind", 1), ("chat_id", 1)], unique=True)

            await self.admins.create_index("user_id", unique=True)
            await self.mutes.create_index("user_id", unique=True)

            await self.backups.create_index("created_at")

            await self.verification_sessions.create_index("session_id", unique=True)
            await self.verification_sessions.create_index("user_id")
            await self.verification_sessions.create_index("access_token")
            await self.verification_sessions.create_index("verification_status")
            # TTL sweep: Mongo removes the document once `ttl_at` (a real BSON
            # date, set to max verification time + a grace period) is in the
            # past. This is a storage-hygiene backstop only — verification
            # correctness never depends on the document still existing;
            # session status/expiration is always evaluated from
            # `created_at`/`expiration` at read time (see triss.services.shortener).
            await self.verification_sessions.create_index("ttl_at", expireAfterSeconds=0)
        except PyMongoError:
            logger.exception("Failed creating MongoDB indexes (continuing; may already exist).")

    async def _ensure_default_settings(self) -> None:
        existing = await self.settings.find_one({"_id": SETTINGS_DOC_ID})
        if existing is None:
            await self.settings.insert_one(DEFAULT_SETTINGS)
            logger.info("Inserted default settings document.")


database = Database()

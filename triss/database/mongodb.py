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
    "linkdl": {
        "enabled": False, "caption": None,
        # Owner-configurable media for the /linkdl reply (mirrors welcome
        # media below) - media_type "none" keeps the old text-only reply;
        # "photo"/"video"/"animation" send that configured file with the
        # caption, same as welcome media does for /start.
        "media_type": "none",
        "photo_file_id": None,
        "video_file_id": None,
        "animation_file_id": None,
        "spoiler": False,
    },
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
    # Active Log Channel (owner-selectable via /settings -> Log Channel;
    # falls back to the LOG_CHANNEL_ID env value until one is picked).
    "log_channel_id": config.log_channel_id,
    "link_expiry_seconds": None,
    # New feature: when True, EVERY message this bot sends (welcome,
    # force sub prompts, shortener popups, broadcasts, delivered files,
    # admin/settings replies — everything) is sent with Telegram's own
    # `protect_content=True`, which disables forwarding/saving for that
    # message for every recipient, including the owner's own chat. This
    # is applied globally via a single low-level patch on the Pyrogram
    # Client's send_*/copy_message methods (see triss.utils.restrict) —
    # not by touching each of this codebase's ~170 individual send call
    # sites — so toggling it here takes effect immediately everywhere,
    # with no restart required.
    "restrict_content": False,
    # 💎 Premium - manual UPI-payment plans. scanner_file_id is the QR
    # code image; buy_message is the customizable /buy_premium intro
    # (mirrors welcome media). Each plan's price/days are fixed per spec;
    # only "details" (the blurb shown under that plan) is owner-editable.
    "premium": {
        "scanner_file_id": None,
        "scanner_media_type": "photo",
        "upi_id": None,
        "buy_message": {
            "text": None, "media_type": "none", "spoiler": False,
            "photo_file_id": None, "video_file_id": None, "animation_file_id": None,
        },
        "plans": {
            "bronze": {
                "label": "Bronze", "price": 99, "days": 7, "daily_limit": 7,
                "details": "✅ No shortener verification\n📊 7 file deliveries per day",
            },
            "silver": {
                "label": "Silver", "price": 199, "days": 15,
                "details": "✅ No shortener verification\n✅ No Force Sub\n♾️ Unlimited file delivery",
            },
            "gold": {
                "label": "Gold", "price": 299, "days": 30,
                "details": "✅ No shortener verification\n✅ No Force Sub\n♾️ Unlimited file delivery\n✨ More features coming soon",
            },
        },
    },
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
        # New feature ("System Access" / "♻️"): a second, FULLY SEPARATE
        # verification mode with its own Domain/API key/Min-Max Time/
        # popups/Anti-Bypass — completely independent from the fields
        # above ("Old Method" / per-link gating). The two modes are
        # mutually exclusive by design: enabling one automatically
        # disables the other (enforced in triss.handlers.callbacks'
        # shortener:toggle / systemaccess:toggle handlers), so at most
        # one of `enabled` (above) / `system_access.enabled` (below) is
        # ever True at the same time — both False is allowed (no
        # gating at all, same as today).
        #
        # Where Old Method gates ONE link per completed verification,
        # System Access instead grants the user unlimited use of EVERY
        # link in the bot for `access_duration_seconds` starting the
        # moment they complete one verification (see
        # triss.database.models.grant_system_access /
        # has_active_system_access, and triss.handlers.start
        # continue_after_force_sub). Force Sub is a separate, unrelated
        # gate and is never bypassed by an active System Access window.
        "system_access": {
            "enabled": False,
            "domain": None,
            "api_key": None,
            "minimum_seconds": 150,
            "maximum_seconds": 500,
            "tutorial_url": None,
            "verify_message": {"text": None, "photo_file_id": None, "spoiler": False},
            "bypass_message": {"text": None, "photo_file_id": None, "spoiler": False},
            "muted_message": {"text": None, "photo_file_id": None, "spoiler": False},
            "anti_bypass": {"strike_limit": 3, "mute_seconds": 600},
            # How long ONE successful verification grants unlimited bot
            # access for. Owner-selectable via fixed presets only (see
            # triss.utils.keyboards.system_access_duration_menu):
            # 1800 (30m), 3600 (1h), 10800 (3h), 21600 (6h), 43200 (12h),
            # 86400 (24h).
            "access_duration_seconds": 21600,
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
    def join_requests(self) -> AsyncIOMotorCollection:
        return self.db["join_requests"]

    @property
    def keyword_filters(self) -> AsyncIOMotorCollection:
        """🔍 Group filters - one doc per keyword. The content itself lives
        in the Store Channel (chat_id/message_id here are a reference,
        same pattern as genlink/batch). Connected groups (where filters
        are actually allowed to fire) reuse the existing `channels`
        collection with kind="group" - no separate collection needed,
        since a group doesn't need a single "active" one like Store/Log,
        just a plain list."""
        return self.db["keyword_filters"]

    @property
    def premium_users(self) -> AsyncIOMotorCollection:
        return self.db["premium_users"]

    @property
    def premium_requests(self) -> AsyncIOMotorCollection:
        return self.db["premium_requests"]

    @property
    def channels(self) -> AsyncIOMotorCollection:
        """Saved Store/Log channels - one doc per (kind, chat_id), where
        kind is "storage" or "log". Which one is ACTIVE lives in settings
        (storage_channel_id / log_channel_id)."""
        return self.db["channels"]

    @property
    def pending_access(self) -> AsyncIOMotorCollection:
        """The link a user was trying to open when Force Sub stopped
        them - kept in MongoDB (not in-memory) so it survives the 15-min
        session timeout and host restarts/spin-downs."""
        return self.db["pending_access"]

    @property
    def admins(self) -> AsyncIOMotorCollection:
        return self.db["admins"]

    @property
    def mutes(self) -> AsyncIOMotorCollection:
        return self.db["mutes"]

    @property
    def bans(self) -> AsyncIOMotorCollection:
        return self.db["bans"]

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

            # Fix: Join Request mode Force Sub. One record per (chat_id,
            # user_id) - the mere existence of this record means "this
            # user has a pending (or was ever seen submitting a) Join
            # Request for this chat", which is what triss.services.
            # forcesub treats as satisfying that entry - see that
            # module's docstring for the full explanation of why.
            await self.join_requests.create_index([("chat_id", 1), ("user_id", 1)], unique=True)

            await self.channels.create_index([("kind", 1), ("chat_id", 1)], unique=True)
            await self.pending_access.create_index("user_id", unique=True)
            await self.pending_access.create_index("created_at", expireAfterSeconds=86400)

            await self.premium_users.create_index("user_id", unique=True)
            await self.premium_requests.create_index("user_id")

            await self.keyword_filters.create_index("keyword", unique=True)

            await self.admins.create_index("user_id", unique=True)
            await self.mutes.create_index("user_id", unique=True)
            await self.bans.create_index("user_id", unique=True)

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

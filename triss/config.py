"""
triss.config
============
Loads and validates all runtime configuration from environment variables.
No secret is ever logged. Fail fast (at startup) if a required variable
is missing or malformed, rather than failing later inside a handler.
"""

from __future__ import annotations

import os
import sys
import logging
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("triss.config")


def _get_required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        logger.critical("Missing required environment variable: %s", name)
        sys.exit(f"[triss] FATAL: missing required environment variable '{name}'. "
                  f"Copy .env.example to .env and fill it in.")
    return value


def _get_optional_int(name: str) -> Optional[int]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.critical("Environment variable %s must be an integer, got %r", name, raw)
        sys.exit(f"[triss] FATAL: environment variable '{name}' must be an integer.")


def _get_required_int(name: str) -> int:
    value = _get_optional_int(name)
    if value is None:
        logger.critical("Missing required environment variable: %s", name)
        sys.exit(f"[triss] FATAL: missing required environment variable '{name}'.")
    return value


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    bot_token: str

    mongo_uri: str
    database_name: str

    owner_id: int
    storage_channel_id: Optional[int]
    log_channel_id: Optional[int]

    port: int = field(default=8080)
    session_name: str = field(default="triss_bot")

    # Public HTTPS base URL of THIS bot's own web server (Railway/Render
    # give you one automatically; on a VPS you'd front it with a domain +
    # reverse proxy). Only needed if you want the branded "Verifying..."
    # landing page between the shortener and Telegram - see
    # triss/web/server.py's /v/<session_id> route. If left blank, the
    # shortener flow falls back to linking straight to the Telegram deep
    # link (no landing page), exactly as before - this is optional
    # cosmetic polish, never a security requirement.
    public_base_url: Optional[str] = field(default=None)

    # Timeouts / limits (sensible defaults, not user secrets)
    session_state_timeout_seconds: int = field(default=15 * 60)
    default_link_expiry_seconds: Optional[int] = field(default=None)  # None = never expires by default
    flood_wait_max_retries: int = field(default=5)

    # HMAC/hash secret used to bind verification proofs to their session.
    # Falls back to a value derived from BOT_TOKEN if not explicitly set,
    # so existing deployments keep working, but setting it explicitly is
    # strongly recommended (see .env.example).
    verification_secret: str = field(default="")

    def masked(self) -> dict:
        """Safe-for-logs representation. Never log real secrets/IDs verbatim in bulk."""
        return {
            "api_id": "***" if self.api_id else None,
            "bot_token_set": bool(self.bot_token),
            "mongo_uri_set": bool(self.mongo_uri),
            "database_name": self.database_name,
            "owner_id_set": bool(self.owner_id),
            "storage_channel_configured": self.storage_channel_id is not None,
            "log_channel_configured": self.log_channel_id is not None,
            "port": self.port,
            "public_base_url_configured": self.public_base_url is not None,
        }

def load_config() -> Config:
    api_id = _get_required_int("API_ID")
    api_hash = _get_required("API_HASH")
    bot_token = _get_required("BOT_TOKEN")

    mongo_uri = _get_required("MONGO_URI")
    database_name = os.environ.get("DATABASE_NAME", "triss").strip() or "triss"

    owner_id = _get_required_int("OWNER_ID")

    # These two are allowed to be unset at first boot; they can be configured
    # later from inside the bot via /settings -> Store Channel, and LOG_CHANNEL_ID
    # is fully optional per spec.
    storage_channel_id = _get_optional_int("STORAGE_CHANNEL_ID")
    log_channel_id = _get_optional_int("LOG_CHANNEL_ID")

    port = _get_optional_int("PORT") or 8080

    public_base_url = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/") or None

    verification_secret = os.environ.get("VERIFICATION_SECRET", "").strip()
    if not verification_secret:
        import hashlib
        verification_secret = hashlib.sha256(f"triss-verification::{bot_token}".encode()).hexdigest()
        logger.warning(
            "VERIFICATION_SECRET is not set; deriving one from BOT_TOKEN. "
            "Set VERIFICATION_SECRET explicitly in production (see .env.example)."
        )

    cfg = Config(
        api_id=api_id,
        api_hash=api_hash,
        bot_token=bot_token,
        mongo_uri=mongo_uri,
        database_name=database_name,
        owner_id=owner_id,
        storage_channel_id=storage_channel_id,
        log_channel_id=log_channel_id,
        port=port,
        verification_secret=verification_secret,
        public_base_url=public_base_url,
    )
    logger.info("Configuration loaded: %s", cfg.masked())
    return cfg


config = load_config()



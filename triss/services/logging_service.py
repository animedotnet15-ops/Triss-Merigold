"""
triss.services.logging_service
===============================
Sends event notices to LOG_CHANNEL_ID if it is configured. Silently
disabled (with a one-time warning) if no log channel is configured.

Per spec, the log channel carries ONLY this fixed 6-line format, for
ONLY these status types - nothing else (no raw files, no captions, no
link text, no tokens):

    Name : <first name or ->
    Username : <@username or ->
    User id : <numeric id>
    Date : <YYYY-MM-DD>
    Time : <HH:MM:SS UTC>
    Status : <Bot Start | Verify Complete | Bypass Detected | Mute | Ban | Linkdl Files>

Six functions, one per status, all calling the single private
`_send_status` formatter so every entry is byte-for-byte the same shape:
  a) log_bot_start        - Status: Bot Start        (triss.handlers.start, both plain AND token starts)
  b) log_verified          - Status: Verify Complete   (triss.handlers.start, on successful shortener verification)
  c) log_bypass_detected   - Status: Bypass Detected   (triss.handlers.start, on the BYPASS outcome)
  d) log_user_muted        - Status: Mute              (triss.handlers.admin, on /mute)
  e) log_user_banned       - Status: Ban               (triss.handlers.admin, on /ban)
  f) log_linkdl_files      - Status: Linkdl Files      (triss.handlers.linkdl, alongside the copied file)

NOTE: genlink/batch link creation deliberately does NOT log here anymore
- only the 6 statuses above are allowed in the log channel per spec
("Na Ippa mention pannirukke mattutha log channel la varanum, vera
edhum vara kudathu"). genlink/batch still store into the Store Channel
+ MongoDB exactly as before; that's unrelated to this log channel.
"""

from __future__ import annotations

import logging
import time

from pyrogram import Client
from pyrogram.errors import RPCError

from triss.config import config

logger = logging.getLogger("triss.logging_service")


def _date() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _time() -> str:
    return time.strftime("%H:%M:%S UTC", time.gmtime())


async def _send_status(client: Client, status: str, user_id: int,
                        username: str | None, first_name: str | None) -> None:
    if not config.log_channel_id:
        return
    text = (
        f"Name : {first_name or '-'}\n"
        f"Username : {('@' + username) if username else '-'}\n"
        f"User id : {user_id}\n"
        f"Date : {_date()}\n"
        f"Time : {_time()}\n"
        f"Status : {status}"
    )
    try:
        await client.send_message(config.log_channel_id, text)
    except RPCError:
        logger.warning("Failed to deliver log event to LOG_CHANNEL_ID.", exc_info=True)
    except Exception:
        logger.warning("Unexpected error sending log event.", exc_info=True)


# --- a) bot start -----------------------------------------------------------

async def log_bot_start(client: Client, user_id: int, username: str | None,
                         first_name: str | None, *, has_token: bool = False) -> None:
    await _send_status(client, "Bot Start", user_id, username, first_name)


# --- b) verified --------------------------------------------------------------

async def log_verified(client: Client, user_id: int, username: str | None,
                        first_name: str | None, access_token: str | None = None) -> None:
    await _send_status(client, "Verify Complete", user_id, username, first_name)


# --- c) bypass detected ---------------------------------------------------------

async def log_bypass_detected(client: Client, user_id: int, username: str | None,
                               first_name: str | None, strike_count: int = 0,
                               strike_limit: int = 0) -> None:
    await _send_status(client, "Bypass Detected", user_id, username, first_name)


# --- d) muted --------------------------------------------------------------------

async def log_user_muted(client: Client, user_id: int, muted_by: int | None = None,
                          reason: str | None = None,
                          username: str | None = None, first_name: str | None = None) -> None:
    await _send_status(client, "Mute", user_id, username, first_name)


# --- e) banned -------------------------------------------------------------------

async def log_user_banned(client: Client, user_id: int, banned_by: int | None = None,
                           reason: str | None = None,
                           username: str | None = None, first_name: str | None = None) -> None:
    await _send_status(client, "Ban", user_id, username, first_name)


# --- f) linkdl files ---------------------------------------------------------------

async def log_linkdl_files(client: Client, user_id: int, username: str | None,
                            first_name: str | None) -> None:
    await _send_status(client, "Linkdl Files", user_id, username, first_name)

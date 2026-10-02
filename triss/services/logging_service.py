"""
triss.services.logging_service
===============================
Sends event notices to the active Log Channel (triss.database.models.
get_active_channel_id("log")). Silently disabled if none is set.

    Name : <first name or ->
    Username : <@username or ->
    ID : <numeric id>
    Date : <DD-MM-YYYY>
    Time : <HH:MM:SS IST>                      (Tamil Nadu / India time, UTC+5:30)
    Status : <Bot Start | Verify Complete | Bypass Detected | Mute | Ban | Linkdl Files>
    Verify Time : <N seconds>                  (Verify Complete only - link generated -> verification completed)
    Delivery Files : <link>                    (only when this event actually delivered content)

Six functions, one per status, all calling the single private
`_send_status` formatter so every entry is the same shape:
  a) log_bot_start        - Status: Bot Start
  b) log_verified          - Status: Verify Complete  (+ Verify Time, + Delivery Files)
  c) log_bypass_detected   - Status: Bypass Detected
  d) log_user_muted        - Status: Mute
  e) log_user_banned       - Status: Ban
  f) log_linkdl_files      - Status: Linkdl Files      (+ Delivery Files)
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from pyrogram import Client
from pyrogram.errors import RPCError

from triss.database import models as db

logger = logging.getLogger("triss.logging_service")

IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist() -> datetime:
    return datetime.now(timezone.utc).astimezone(IST)


def _date() -> str:
    return _now_ist().strftime("%d-%m-%Y")


def _time() -> str:
    return _now_ist().strftime("%I:%M:%S %p IST")


async def _send_status(client: Client, status: str, user_id: int,
                        username: str | None, first_name: str | None,
                        extra: list[tuple[str, str]] | None = None) -> None:
    log_channel_id = await db.get_active_channel_id("log")
    if not log_channel_id:
        return
    lines = [
        f"Name : {first_name or '-'}",
        f"Username : {('@' + username) if username else '-'}",
        f"ID : {user_id}",
        f"Date : {_date()}",
        f"Time : {_time()}",
        f"Status : {status}",
    ]
    for label, value in (extra or []):
        lines.append(f"{label} : {value}")
    try:
        await client.send_message(log_channel_id, "\n".join(lines))
    except RPCError:
        logger.warning("Failed to deliver log event to the Log Channel.", exc_info=True)
    except Exception:
        logger.warning("Unexpected error sending log event.", exc_info=True)


# --- a) bot start -----------------------------------------------------------

async def log_bot_start(client: Client, user_id: int, username: str | None,
                         first_name: str | None, *, has_token: bool = False) -> None:
    await _send_status(client, "Bot Start", user_id, username, first_name)


# --- b) verified --------------------------------------------------------------

async def log_verified(client: Client, user_id: int, username: str | None,
                        first_name: str | None, access_token: str | None = None,
                        verify_seconds: float | int | None = None,
                        delivered_link: str | None = None) -> None:
    extra = []
    if verify_seconds is not None:
        extra.append(("Verify Time", f"{round(verify_seconds)} seconds"))
    if delivered_link:
        extra.append(("Delivery Files", delivered_link))
    await _send_status(client, "Verify Complete", user_id, username, first_name, extra)


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
                            first_name: str | None, delivered_link: str | None = None) -> None:
    extra = [("Delivery Files", delivered_link)] if delivered_link else []
    await _send_status(client, "Linkdl Files", user_id, username, first_name, extra)

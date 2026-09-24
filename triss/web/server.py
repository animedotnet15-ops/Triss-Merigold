"""
triss.web.server
=================
  GET /health       -> "OK" (Railway/Render liveness probe)
  GET /              -> "OK" (same, for platforms that probe "/")
  GET /dl/<token>    -> item 6 (/linkdl): streams a stored file straight
                        to the browser as a real HTTP download (unlike
                        every other link in this bot, which delivers via
                        a Telegram deep link/copy_message instead).

Shortener verification does NOT use a web route — it is handled entirely
inside Telegram via the `/start verify_<session_id><proof>` deep link
(see triss.services.shortener and triss.handlers.start). PUBLIC_BASE_URL
is ONLY needed for the /dl/<token> route below; every other feature in
this bot works with it left unset.

--------------------------------------------------------------------------
/dl/<token> — HOW IT WORKS AND WHAT IT DOES NOT DO
--------------------------------------------------------------------------
- STATELESS: no database record backs this route at all (explicit spec:
  "database la add aga vendam"). `token` is a self-contained, HMAC-signed
  reference to a message id in LOG_CHANNEL_ID (see
  triss.utils.linkdl_token) — decoding + verifying the signature IS the
  entire validity check, there is nothing else to look up.
- Requires triss.database.mongodb DEFAULT_SETTINGS "linkdl.enabled" to
  be True ("🌍 Public Use" toggle in /settings) - if the owner turns
  this off, EVERY /dl/<token> URL stops working immediately, even for
  links already shared - this is the ONLY revocation mechanism, since
  there's no per-link record to revoke individually.
- Streams the file chunk-by-chunk from Telegram via the bot's own
  Client.stream_media() (see triss.bot.app) straight into the HTTP
  response - the file is never buffered whole in memory or written to
  disk on this server, so this scales to large files on small hosts.
- Sets Content-Disposition: attachment so the browser downloads the
  file directly rather than trying to render it inline.
- Deliberately does NOT enforce Force Sub or Shortener verification -
  those are Telegram-side gates (a "join this channel" button, a
  Telegram deep link) that make no sense against a raw HTTP request;
  the ONLY gate here is the enabled toggle plus the token's own
  signature check, both already covered above.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

from aiohttp import web
from pyrogram.errors import RPCError

from triss.config import config
from triss.database import models as db
from triss.utils.linkdl_token import decode_linkdl_token

logger = logging.getLogger("triss.web")

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


async def health(_request: web.Request) -> web.Response:
    return web.Response(text="OK")


def _guess_filename(message) -> str:
    for attr in ("document", "video", "audio", "animation", "voice", "video_note"):
        media = getattr(message, attr, None)
        if media is not None:
            name = getattr(media, "file_name", None)
            if name:
                return name
    if message.photo:
        return f"photo_{message.photo.file_unique_id}.jpg"
    return "file"


def _guess_size(message) -> int | None:
    for attr in ("document", "video", "audio", "animation", "voice", "video_note", "photo"):
        media = getattr(message, attr, None)
        if media is not None:
            return getattr(media, "file_size", None)
    return None


def _guess_mime(message) -> str:
    for attr in ("document", "video", "audio", "animation", "voice", "video_note"):
        media = getattr(message, attr, None)
        if media is not None:
            return getattr(media, "mime_type", None) or "application/octet-stream"
    if message.photo:
        return "image/jpeg"
    return "application/octet-stream"


async def download_by_token(request: web.Request) -> web.StreamResponse:
    """STATELESS by design (see triss.utils.linkdl_token) — `token`
    itself encodes the LOG_CHANNEL_ID message id plus an HMAC signature.
    There is no database record to look up here at all: the signature
    check IS the validity check. This means a linkdl link can't be
    individually revoked/expired - only turned off globally via the
    linkdl.enabled toggle checked first below."""
    token = request.match_info.get("token", "")
    if not _TOKEN_RE.match(token):
        raise web.HTTPBadRequest(text="Invalid link.")

    settings = await db.get_settings()
    if not settings.get("linkdl", {}).get("enabled"):
        raise web.HTTPForbidden(text="Direct downloads are currently disabled by the bot owner.")

    if not config.log_channel_id:
        raise web.HTTPNotFound(text="Direct downloads aren't configured.")

    message_id = decode_linkdl_token(token)
    if message_id is None:
        raise web.HTTPNotFound(text="This link is invalid.")

    # Imported here (not at module level) to avoid a circular import —
    # triss.bot fully finishes loading (including all its handler
    # imports) before main.py ever imports triss.web.server, so this is
    # safe, but only once triss.bot itself is done initializing.
    from triss.bot import app as bot_client

    try:
        message = await bot_client.get_messages(config.log_channel_id, message_id)
    except RPCError:
        logger.warning("Could not fetch log-channel message for /dl/%s.", token, exc_info=True)
        raise web.HTTPNotFound(text="This file is no longer available.")

    if message is None or message.empty:
        raise web.HTTPNotFound(text="This file is no longer available.")

    filename = _guess_filename(message)
    size = _guess_size(message)
    mime = _guess_mime(message)

    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": mime,
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )
    if size:
        response.content_length = size
    await response.prepare(request)

    try:
        async for chunk in bot_client.stream_media(message):
            await response.write(chunk)
    except (RPCError, ConnectionResetError):
        logger.warning("Download stream interrupted for /dl/%s.", token, exc_info=True)
    finally:
        await response.write_eof()
    return response


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/", health)
    app.router.add_get("/dl/{token}", download_by_token)
    return app


async def start_web_server() -> web.AppRunner:
    aio_app = build_app()
    runner = web.AppRunner(aio_app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=config.port)
    await site.start()
    logger.info("Web server listening on 0.0.0.0:%s (GET /health, GET /dl/<token>).", config.port)
    return runner
  

"""
triss.web.server
=================
Exposes:

  GET /health          -> "OK" (Railway/Render liveness probe)
  GET /                -> "OK" (same, for platforms that probe "/")
  GET /v/<session_id>   -> optional branded "Verifying..." landing page
                            shown between the shortener and Telegram.

--------------------------------------------------------------------------
WHAT /v/<session_id> DOES AND DOES NOT DO — READ BEFORE CHANGING
--------------------------------------------------------------------------
This route is COSMETIC ONLY. It performs NO verification of its own — it
never reads or writes triss.database.models.verification_sessions, never
checks timing, and never issues or checks the proof. Its only job is to
show a branded "please wait" animation for a couple of seconds and then
redirect the browser to the real Telegram deep link
(`https://t.me/<bot>?start=verify_<session_id><proof>`), which is where
ALL real verification happens, entirely server-side, exactly as before
this route existed — see triss.services.shortener.evaluate_verification().

This route is used only if PUBLIC_BASE_URL is configured (see
triss.config); otherwise the shortener link points straight at the
Telegram deep link and this route is simply never linked to. Either way,
the security properties of the verification system are IDENTICAL - this
route can be removed entirely with zero security impact, it exists
purely for a nicer-looking user experience during the redirect hop.

The page intentionally does NOT claim to be doing "payment", "security",
or "human" verification of any kind, and does NOT include a fake
CAPTCHA/"I'm not a robot" button — it truthfully says it is redirecting
the user back to Telegram. See triss/web/templates/verify_landing.html.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from aiohttp import web

from triss.config import config

logger = logging.getLogger("triss.web")

TEMPLATE_PATH = Path(__file__).parent / "templates" / "verify_landing.html"
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_template_cache: str | None = None


def _load_template() -> str:
    global _template_cache
    if _template_cache is None:
        _template_cache = TEMPLATE_PATH.read_text(encoding="utf-8")
    return _template_cache


async def health(_request: web.Request) -> web.Response:
    return web.Response(text="OK")


async def verify_landing(request: web.Request) -> web.Response:
    """Serves the cosmetic waiting page. `session_id` is only used here to
    shape-validate the URL (so this route can't be abused as an open
    redirector to an arbitrary destination) - it is never looked up in
    the database from this handler. The bot's own username is injected
    so the page's client-side JS can build the correct deep link without
    needing any server round-trip."""
    session_id = request.match_info.get("session_id", "")
    if not _SESSION_ID_RE.match(session_id):
        raise web.HTTPBadRequest(text="Invalid session id.")

    bot_username = request.app.get("bot_username") or ""
    html = _load_template().replace("{{BOT_USERNAME}}", bot_username)
    return web.Response(text=html, content_type="text/html")


def build_app(bot_username: str = "") -> web.Application:
    app = web.Application()
    app["bot_username"] = bot_username
    app.router.add_get("/health", health)
    app.router.add_get("/", health)
    app.router.add_get("/v/{session_id}", verify_landing)
    return app


async def start_web_server(bot_username: str = "") -> web.AppRunner:
    aio_app = build_app(bot_username)
    runner = web.AppRunner(aio_app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=config.port)
    await site.start()
    logger.info("Web server listening on 0.0.0.0:%s (GET /health, GET /v/<session_id>).", config.port)
    return runner
  

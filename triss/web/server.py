"""
triss.web.server
=================
Minimal aiohttp server exposing only:

  GET /health   -> "OK" (Railway/Render liveness probe)
  GET /         -> "OK" (same, for platforms that probe "/")

Shortener verification does NOT use a web route in this build — it is
handled entirely inside Telegram via the `/start verify_<session_id><proof>`
deep link (see triss.services.shortener and triss.handlers.start). No
PUBLIC_BASE_URL or public callback/webhook URL is required for Shortener
to work; this server exists solely so hosting platforms (Railway/Render)
have something to health-check.
"""

from __future__ import annotations

import logging

from aiohttp import web

from triss.config import config

logger = logging.getLogger("triss.web")


async def health(_request: web.Request) -> web.Response:
    return web.Response(text="OK")


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/", health)
    return app


async def start_web_server() -> web.AppRunner:
    aio_app = build_app()
    runner = web.AppRunner(aio_app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=config.port)
    await site.start()
    logger.info("Health server listening on 0.0.0.0:%s (GET /health).", config.port)
    return runner

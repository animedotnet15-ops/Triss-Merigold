"""
Triss File Store Bot — entrypoint.

Starts the aiohttp health server and the Telegram client together on one
event loop, and shuts both down cleanly on SIGINT/SIGTERM.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("triss.main")


async def _run() -> None:
    # Imported here (after logging is configured) so config-loading /
    # startup log lines use the same format.
    from triss.bot import startup, shutdown
    from triss.web.server import start_web_server

    stop_event = asyncio.Event()

    def _request_stop(*_args) -> None:
        logger.info("Shutdown signal received.")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                loop.add_signal_handler(sig, _request_stop)
            except NotImplementedError:
                # add_signal_handler isn't available on some platforms (e.g. Windows)
                signal.signal(sig, lambda *_: _request_stop())

    web_runner = await start_web_server()
    try:
        await startup()
    except Exception:
        logger.critical("Fatal error during bot startup.", exc_info=True)
        await web_runner.cleanup()
        sys.exit(1)

    # The bot's @username is only known after app.start() inside startup()
    # (see triss/bot.py) - inject it into the already-running web app so
    # the /v/<session_id> landing page (triss/web/server.py) can build the
    # correct Telegram deep link. Health checks work fine before this line
    # runs; only that one cosmetic page needs it.
    from triss.bot import app as bot_client
    web_runner.app["bot_username"] = getattr(bot_client, "username", "") or ""

    logger.info("Triss File Store Bot is up and running.")
    await stop_event.wait()

    await shutdown()
    await web_runner.cleanup()


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
    

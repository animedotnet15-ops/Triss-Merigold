"""
triss.utils.maintenance
========================
In-memory cache for the "maintenance" setting, mirroring the exact same
pattern already used for the admin cache (triss.utils.auth) and the
Restrict Content cache (triss.utils.restrict): warmed once from MongoDB
at startup, updated immediately the instant the owner toggles it, so the
global maintenance gate (triss.handlers.maintenance_gate) - which runs
on EVERY incoming message and callback query - never needs a MongoDB
round-trip in that hot path.
"""

from __future__ import annotations

_maintenance_enabled_cache: bool = False


def set_maintenance_cache(value: bool) -> None:
    """Called once at startup (triss.bot.startup) to warm the cache from
    MongoDB, and again immediately after every maintenance:on/off toggle
    (triss.handlers.callbacks) so the change is live with no restart."""
    global _maintenance_enabled_cache
    _maintenance_enabled_cache = bool(value)


def get_maintenance_cache() -> bool:
    return _maintenance_enabled_cache

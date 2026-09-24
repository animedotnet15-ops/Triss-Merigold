"""
triss.utils.linkdl_token
==========================
Item 6 (/linkdl) tokens are STATELESS by design — no database record is
created for a direct-download link (per spec: "database la add aga
vendam" - don't add it to the database). The token itself encodes
everything needed to serve the file: the LOG_CHANNEL_ID message id, plus
an HMAC signature (using config.verification_secret, domain-separated
with a distinct prefix so these can never be confused with the
Shortener's own proof tokens even though they share a secret) that
proves the token wasn't forged/guessed - since there is no database
record to check "does this token exist" against, the signature is the
ONLY thing standing between a random guess and an arbitrary log-channel
message being served. A 64-bit HMAC over an attacker-unknown secret is
computationally infeasible to guess by brute force.

Trade-off, explicit per spec: because nothing is persisted, a linkdl
link can never be individually revoked or set to expire - it works for
as long as (a) the log channel message still exists and (b) linkdl is
globally enabled (settings.linkdl.enabled - see triss.handlers.linkdl).
If per-link revocation is ever needed later, that requires reverting to
a database-backed design (e.g. the one /genlink still uses).
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from triss.config import config

_DOMAIN_PREFIX = "linkdl"


def _sign(message_id: int) -> str:
    key = config.verification_secret.encode()
    payload = f"{_DOMAIN_PREFIX}:{message_id}".encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()[:16]


def encode_linkdl_token(message_id: int) -> str:
    raw = f"{message_id}.{_sign(message_id)}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_linkdl_token(token: str) -> int | None:
    """Returns the log-channel message id if `token` carries a valid
    signature, else None (forged, corrupted, or truncated token)."""
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        message_id_str, sig = raw.split(".", 1)
        message_id = int(message_id_str)
    except Exception:
        return None
    if not hmac.compare_digest(sig, _sign(message_id)):
        return None
    return message_id

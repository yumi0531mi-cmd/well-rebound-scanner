"""Small authentication helpers for the runup web boundary."""
from __future__ import annotations

import hmac

ADMIN_TOKEN_ENV = "WELLSCAN_ADMIN_TOKEN"
ADMIN_TOKEN_MIN_LENGTH = 24


def admin_token_configured(secret: str | None) -> bool:
    """Reject an absent or trivially weak administrator secret."""
    return isinstance(secret, str) and len(secret) >= ADMIN_TOKEN_MIN_LENGTH


def admin_token_valid(secret: str | None, supplied: str | None) -> bool:
    """Constant-time comparison; an unset or weak secret disables the page."""
    if not admin_token_configured(secret) or not isinstance(supplied, str):
        return False
    return hmac.compare_digest(secret, supplied)

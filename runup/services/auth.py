"""Opaque server-verified writer grants; public UI cannot self-assert permissions."""
from __future__ import annotations

import dataclasses
import hashlib
import hmac
import os
from datetime import UTC, datetime, timedelta

from wellscan.web_status import ADMIN_TOKEN_ENV, admin_token_configured, admin_token_valid


@dataclasses.dataclass(frozen=True)
class WriterGrant:
    actor: str
    expires_at: str
    signature: str


def _sign(secret, actor, expiry):
    return hmac.new(secret.encode(), (actor+"|"+expiry).encode(), hashlib.sha256).hexdigest()


def login(supplied, actor="owner", as_of=None):
    import config
    at = as_of or datetime.now(UTC)
    secret = os.environ.get(ADMIN_TOKEN_ENV)
    if not admin_token_valid(secret, supplied):
        return None
    if at.tzinfo is None or not isinstance(actor,str) or not actor:
        return None
    expiry = (at+timedelta(seconds=config.RUNUP_UI_POLICY["writer_grant_seconds"])).isoformat()
    return WriterGrant(actor,expiry,_sign(secret,actor,expiry))


def verified(grant, as_of=None):
    secret = os.environ.get(ADMIN_TOKEN_ENV)
    if not admin_token_configured(secret) or not isinstance(grant,WriterGrant):
        return False
    try:
        expiry = datetime.fromisoformat(grant.expires_at)
        at = as_of or datetime.now(UTC)
        return expiry.tzinfo is not None and at.tzinfo is not None and at < expiry and hmac.compare_digest(
            grant.signature,_sign(secret,grant.actor,grant.expires_at))
    except (TypeError,ValueError):
        return False


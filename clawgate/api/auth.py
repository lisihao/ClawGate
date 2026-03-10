"""API Key Authentication for ClawGate.

Reads valid keys from CLAWGATE_API_KEYS environment variable (comma-separated).
Can be disabled with CLAWGATE_AUTH_ENABLED=false for backward compatibility.

Usage in routes:
    @app.post("/v1/chat/completions", dependencies=[Depends(verify_api_key)])
"""

import hmac
import os
import logging
from typing import Optional

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger("clawgate.api.auth")

# Lazy-init bearer scheme (auto_error=False so we handle missing header ourselves)
_bearer_scheme = HTTPBearer(auto_error=False)


def _is_auth_enabled() -> bool:
    """Check if authentication is enabled (default: true)."""
    return os.getenv("CLAWGATE_AUTH_ENABLED", "true").lower() not in ("false", "0", "no")


def _get_valid_keys() -> set:
    """Load valid API keys from environment."""
    raw = os.getenv("CLAWGATE_API_KEYS", "")
    if not raw.strip():
        return set()
    return {k.strip() for k in raw.split(",") if k.strip()}


async def verify_api_key(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(_bearer_scheme),
):
    """FastAPI dependency — verify Bearer token against allowed keys.

    Skip verification when:
      - CLAWGATE_AUTH_ENABLED=false
      - No keys configured (warn once and allow)
    """
    if not _is_auth_enabled():
        return None

    valid_keys = _get_valid_keys()
    if not valid_keys:
        # Auth enabled but no keys configured — server misconfiguration.
        # Reject all requests to avoid silent security hole.
        logger.error(
            "[Auth] CLAWGATE_AUTH_ENABLED=true but CLAWGATE_API_KEYS is empty — "
            "rejecting request. Set CLAWGATE_API_KEYS or CLAWGATE_AUTH_ENABLED=false."
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "message": "Server auth misconfiguration: no API keys configured.",
                    "type": "server_error",
                }
            },
        )

    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "message": "Missing API key. Provide 'Authorization: Bearer sk-claw-...' header.",
                    "type": "authentication_error",
                }
            },
        )

    token = credentials.credentials
    # Constant-time comparison to prevent timing attacks (C2 fix)
    if not any(hmac.compare_digest(token, k) for k in valid_keys):
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "message": "Invalid API key.",
                    "type": "authentication_error",
                }
            },
        )

    return token

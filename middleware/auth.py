"""
API key authentication dependency.

Usage in router:
    from middleware.auth import verify_api_key
    router = APIRouter(dependencies=[Depends(verify_api_key)])

When CARETAKER_API_KEY is not set in the environment the check is skipped
entirely (open/dev mode). Set it in .env to enable protection.
"""

import os

from fastapi import Header, HTTPException
from fastapi.security import APIKeyHeader

_API_KEY = os.getenv("CARETAKER_API_KEY")

_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(x_api_key: str = Header(default=None, alias="X-API-Key")) -> None:
    """FastAPI dependency — raises 401 if key is configured and doesn't match."""
    if not _API_KEY:
        # No key configured → dev/local mode, all requests allowed
        return
    if x_api_key != _API_KEY:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key. Set X-API-Key header.",
        )

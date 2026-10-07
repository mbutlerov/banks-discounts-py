"""Optional read-access gate, independent from administrative authorization."""

from __future__ import annotations

import secrets

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class APIAccessMiddleware:
    def __init__(self, app: ASGIApp, api_access_key: str | None) -> None:
        self.app = app
        self.expected_key = api_access_key.encode("ascii") if api_access_key else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.expected_key is None:
            await self.app(scope, receive, send)
            return

        if scope["path"] == "/healthz" and scope["method"] in {"GET", "HEAD"}:
            await self.app(scope, receive, send)
            return

        # Examine the raw list: collapsing duplicate headers can silently accept
        # conflicting credentials. Comparing bytes also handles non-ASCII input.
        provided_keys = [
            value for name, value in scope.get("headers", [])
            if name.lower() == b"x-api-key"
        ]
        if len(provided_keys) != 1 or not secrets.compare_digest(provided_keys[0], self.expected_key):
            response = JSONResponse(
                status_code=401,
                content={"detail": "Credencial de acceso inválida."},
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)

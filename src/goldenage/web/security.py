"""Security response headers for the web application."""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "img-src 'self'; "
    "media-src 'self'; "
    "object-src 'none'; "
    "script-src 'self'; "
    "style-src 'self'"
)
REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("Content-Security-Policy", CONTENT_SECURITY_POLICY),
    ("Referrer-Policy", REFERRER_POLICY),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
)
HSTS_HEADER = ("Strict-Transport-Security", "max-age=31536000; includeSubDomains")


def build_security_headers(*, hsts_enabled: bool) -> tuple[tuple[str, str], ...]:
    """Return the headers applied to every HTTP response."""
    if hsts_enabled:
        return (*SECURITY_HEADERS, HSTS_HEADER)
    return SECURITY_HEADERS


class SecurityHeadersMiddleware:
    """Apply browser security headers to normal and error responses."""

    def __init__(self, app: ASGIApp, *, hsts_enabled: bool = False) -> None:
        self.app = app
        self.headers = build_security_headers(hsts_enabled=hsts_enabled)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_with_security_headers(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                response_headers = MutableHeaders(scope=message)
                for name, value in self.headers:
                    if name not in response_headers:
                        response_headers[name] = value
            await send(message)

        try:
            await self.app(scope, receive, send_with_security_headers)
        except Exception:
            if response_started:
                raise
            error_response = PlainTextResponse(
                "Internal Server Error",
                status_code=500,
                headers=dict(self.headers),
            )
            await error_response(scope, receive, send)

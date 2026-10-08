import asyncio

from starlette.types import ASGIApp, Message, Scope

from goldenage.web.security import (
    CONTENT_SECURITY_POLICY,
    HSTS_HEADER,
    REFERRER_POLICY,
    SECURITY_HEADERS,
    SecurityHeadersMiddleware,
)


def _security_test_app(*, hsts_enabled: bool = False) -> ASGIApp:
    async def application(scope: Scope, receive, send) -> None:
        del receive
        path = scope["path"]
        if path == "/does-not-exist":
            status = 404
            body = b"Not Found"
        elif path.startswith("/static/"):
            status = 200
            body = b"local static asset"
        elif path.startswith("/profiles/"):
            status = 200
            body = b"local profile image"
        else:
            status = 200
            body = b"<main>GoldenAge</main>"
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"text/html; charset=utf-8")],
            }
        )
        await send({"type": "http.response.body", "body": body})

    return SecurityHeadersMiddleware(application, hsts_enabled=hsts_enabled)


def _request(app: ASGIApp, path: str) -> tuple[int, dict[str, str], bytes]:
    messages: list[Message] = []
    request_sent = False

    async def receive() -> Message:
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))
    response_start = next(
        message for message in messages if message["type"] == "http.response.start"
    )
    response_body = b"".join(
        body
        for message in messages
        if message["type"] == "http.response.body"
        and isinstance(body := message.get("body", b""), bytes)
    )
    raw_headers = response_start["headers"]
    assert isinstance(raw_headers, list)
    headers = {name.decode("latin-1"): value.decode("latin-1") for name, value in raw_headers}
    status = response_start["status"]
    assert isinstance(status, int)
    return status, headers, response_body


def _assert_security_headers(response, *, hsts: bool = False) -> None:
    _, headers, _ = response
    assert headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert headers["referrer-policy"] == REFERRER_POLICY
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    if hsts:
        assert headers["strict-transport-security"] == HSTS_HEADER[1]
    else:
        assert "strict-transport-security" not in headers


def test_security_headers_cover_html_static_profile_and_not_found() -> None:
    app = _security_test_app()
    for response in (
        _request(app, "/"),
        _request(app, "/static/htmx-2.0.4.js"),
        _request(app, "/profiles/golden_age_favicon_48.ico"),
        _request(app, "/does-not-exist"),
    ):
        _assert_security_headers(response)


def test_hsts_requires_explicit_secure_https_configuration() -> None:
    _assert_security_headers(_request(_security_test_app(hsts_enabled=True), "/"), hsts=True)


def test_security_headers_cover_unhandled_errors() -> None:
    async def boom(scope: Scope, receive, send) -> None:
        del scope, receive, send
        raise RuntimeError("test failure")

    response = _request(SecurityHeadersMiddleware(boom), "/boom")

    assert response[0] == 500
    _assert_security_headers(response)


def test_security_header_set_is_complete_and_hsts_is_opt_in() -> None:
    assert {name for name, _ in SECURITY_HEADERS} == {
        "Content-Security-Policy",
        "Referrer-Policy",
        "X-Content-Type-Options",
        "X-Frame-Options",
    }

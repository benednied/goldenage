# Web security configuration

## Runtime mode and signing secret

`GOLDENAGE_ENVIRONMENT` selects `development` (the default) or `production`.
Production startup fails when `GOLDENAGE_AUTH_SECRET` is missing, blank, a
placeholder, or too weak. Supply the same randomly generated secret to every
process and instance; it is an HMAC signing key and is never logged.

The development fallback is generated once when a process starts. It is only a
convenience for the in-memory demo and local development. It deliberately does
not provide restart or multi-process session persistence. Local SQLite users
should set a persistent `GOLDENAGE_AUTH_SECRET` as well.

## Cookies and HTTPS

The application centralizes both cookie definitions. The authentication cookie
is `goldenage_session`; the CSRF double-submit cookie is `goldenage_csrf`.
`GOLDENAGE_AUTH_COOKIE_PATH`, `GOLDENAGE_AUTH_COOKIE_DOMAIN`,
`GOLDENAGE_AUTH_COOKIE_SAMESITE`, and `GOLDENAGE_AUTH_SESSION_MAX_AGE` apply to
both cookies. Authentication cookies are always `Secure` in production and are
`HttpOnly`; the CSRF cookie is intentionally readable by same-origin browser
code and is not `HttpOnly`. Logout expires both cookies with the same path and
domain attributes used when setting them. If the application is mounted below
the domain root, set the cookie path to that mount prefix (and ensure requests
to the prefix receive the cookie).

Set `GOLDENAGE_AUTH_COOKIE_SECURE=1` for local HTTPS. A production deployment
must terminate TLS before the browser reaches the application. When a reverse
proxy terminates TLS, configure it to forward the external HTTPS scheme and
host consistently, and restrict that forwarding to the trusted proxy. Do not
enable insecure cookies to compensate for a proxy that loses the HTTPS scheme.

## CSRF behavior

Every state-changing form, including login and onboarding, carries a hidden
`csrf_token` field. HTMX requests also inherit `X-CSRF-Token` from the page.
The server requires a matching cookie plus form field or header, validates the
token signature against the current authentication cookie, and rejects
cross-site Fetch Metadata. Tokens are rotated when a login or password change
creates a new session. Tokens never appear in URLs.

Authentication sessions expire after `GOLDENAGE_AUTH_SESSION_MAX_AGE` seconds,
are invalidated by signing-secret rotation, and are bound to the current local
password hash. A password change therefore invalidates older sessions while
keeping the session that performed the change active.

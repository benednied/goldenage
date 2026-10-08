# SEC-05: local browser assets and security headers

The application now serves its HTMX browser runtime from
`src/goldenage/web/static/htmx-2.0.4.js`. The local asset is same-origin and
the version/source/license record is kept in `HTMX-LICENSE.txt` beside it.
Refreshes must update the asset, version record, and browser smoke test
together; no CDN URL belongs in a template.
The distribution packaging work tracked separately by PKG-01 must include the
JavaScript and license text as package data; source-tree development does not
depend on that packaging change being complete.

The interface uses the local system font stack (`system-ui`, `-apple-system`,
`Segoe UI`, and Georgia fallbacks). This avoids shipping font binaries and
removes Google Fonts requests while retaining a readable fallback on supported
desktop platforms.

`SecurityHeadersMiddleware` applies the enforced same-origin CSP, `nosniff`,
`strict-origin-when-cross-origin`, and frame protection to application,
static, profile-image, not-found, and handled error responses. HSTS is opt-in:
set both `GOLDENAGE_HSTS_ENABLED=1` and `GOLDENAGE_AUTH_COOKIE_SECURE=1` only
when the deployment is HTTPS-only. Local HTTP development intentionally does
not emit HSTS.

The current CSP has no inline-script or inline-style exception. The supported
HTMX surface uses same-origin `hx-*` requests, and the application’s scripts
and stylesheet remain external local files. Image and media sources are also
restricted to the application origin.

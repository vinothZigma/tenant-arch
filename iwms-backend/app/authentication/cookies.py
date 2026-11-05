"""Where a request's access token comes from, and the web auth cookie.

The mobile app sends ``Authorization: Bearer <token>``. The web portals never
see their token: login sets it in an HttpOnly cookie and the browser sends it
back. Each portal (admin / district / local body) has its own cookie so
signing in to one never replaces another's session in the same browser.

A browser request names its portal in the ``X-IWMS-Portal`` header, and the
cookie is only honoured when that header is present. A custom header cannot
be sent cross-origin without passing the CORS allowlist, so this doubles as
the CSRF guard for cookie-authenticated requests.
"""

from django.conf import settings

PORTAL_HEADER = "X-IWMS-Portal"

PORTAL_COOKIES = {
    "admin": "iwms_access",
    "district": "iwms_district_access",
    "localbody": "iwms_lb_access",
}


def _cookie_name(portal):
    """Company and super-admin URLs (/api/t/<slug>/, /api/super/) use a cookie
    of their own (see TenantMiddleware); everything else the portal's."""
    from app.tenancy.context import get_cookie_scope

    name, _ = get_cookie_scope()
    return name or PORTAL_COOKIES[portal]


def _cookie_path():
    from app.tenancy.context import get_cookie_scope

    return get_cookie_scope()[1]


def request_portal(request):
    """The portal a browser request belongs to, or None (mobile / API)."""
    portal = (request.headers.get(PORTAL_HEADER) or "").strip().lower()
    return portal if portal in PORTAL_COOKIES else None


def bearer_token(request):
    auth = request.headers.get("Authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    return "".join(auth.split(" ", 1)[1].split()) or None


def request_token(request):
    """The caller's raw JWT: the Bearer header first, else the portal cookie."""
    token = bearer_token(request)
    if token:
        return token
    portal = request_portal(request)
    if not portal:
        return None
    return request.COOKIES.get(_cookie_name(portal)) or None


def set_auth_cookie(response, portal, token, max_age):
    response.set_cookie(
        _cookie_name(portal),
        token,
        max_age=max_age,
        path=_cookie_path(),
        domain=settings.AUTH_COOKIE_DOMAIN,
        secure=settings.AUTH_COOKIE_SECURE,
        httponly=True,
        samesite=settings.AUTH_COOKIE_SAMESITE,
    )


def clear_auth_cookie(response, portal):
    response.delete_cookie(
        _cookie_name(portal),
        path=_cookie_path(),
        domain=settings.AUTH_COOKIE_DOMAIN,
        samesite=settings.AUTH_COOKIE_SAMESITE,
    )

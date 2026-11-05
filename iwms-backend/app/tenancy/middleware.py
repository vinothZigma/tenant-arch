"""Pick the company (and so the database) for every request.

/api/t/<slug>/<rest>     Company URL. The slug selects the company; the path
                         is rewritten to /api/v1/<rest>, so every existing
                         IWMS endpoint works per company, plus the workspace
                         API under ws/. A token for another company -> 401.
/api/super/...           Super admin console: no company (views choose).
/api/v1/, /api/mobile/,  Legacy URLs (mobile app, old web login): the company
/api/desktop/            comes from the token's company claim. A super admin
                         token may name one with the X-IWMS-Company header or
                         a company_id / company_unique_id parameter. A login
                         without a token finds the company by username.

Must sit right after CorsMiddleware (before ModulePermissionMiddleware,
which already queries company tables).
"""
import json
import logging
import re

import jwt
from django.conf import settings
from django.http import JsonResponse

from app.authentication.cookies import request_token

from .context import reset_cookie_scope, set_cookie_scope, tenant_context
from .db import register_tenant_db
from .router import per_db_mode

logger = logging.getLogger(__name__)

TENANT_URL = re.compile(r"^/api/t/(?P<slug>[a-z0-9-]{1,40})/(?P<rest>.*)$")
LEGACY_PREFIXES = ("/api/v1/", "/api/mobile/", "/api/desktop/")
SUPER_PREFIX = "/api/super/"
COMPANY_HEADER = "HTTP_X_IWMS_COMPANY"
LOGIN_FIELDS = ("username", "login", "email", "mobile", "mobile_no", "phone")


def company_cookie_name(slug):
    return "iwms_t_" + slug.replace("-", "_")


def _deny(status, detail):
    return JsonResponse({"detail": detail}, status=status)


def _payload(request):
    """The request's verified token claims, or None."""
    token = request_token(request)
    if not token:
        return None
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None  # authentication rejects it later with a proper message


def is_super_payload(payload):
    return bool(payload) and (payload.get("scope") == "super" or payload.get("user_type") == "platform")


def _company(**lookup):
    from app.models.superadmin_masters.company import Company

    return Company.objects.filter(is_deleted=False, **lookup).exclude(db_name=None).first()


def _company_by_key(key):
    key = (key or "").strip()
    if not key:
        return None
    return _company(slug=key) or _company(unique_id=key)


def _json_body(request):
    if request.method not in ("POST", "PUT", "PATCH"):
        return {}
    if "json" not in (request.content_type or ""):
        return {}
    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _company_named_by_request(request):
    """Super admin on a legacy URL: header, query string or JSON body."""
    key = request.META.get(COMPANY_HEADER)
    if not key:
        for name in ("company_id", "company_unique_id", "company"):
            if request.GET.get(name):
                key = request.GET[name]
                break
    if not key:
        body = _json_body(request)
        key = body.get("company_id") or body.get("company_unique_id")
    return _company_by_key(key) if isinstance(key, str) else None


def find_company_for_login(identifier):
    """The one company whose DB has a login called `identifier` (staff,
    leaders), for logins on URLs without a company slug. None if no company
    or several companies match."""
    from django.db.models import Q

    from app.models.masters.leader_management.district_leader_login import DistrictLeaderLogin
    from app.models.masters.leader_management.panchayat_leader_login import PanchayatLeaderLogin
    from app.models.superadmin.staff_management.staffcreation import Staffcreation
    from app.models.superadmin_masters.company import Company

    identifier = (identifier or "").strip()
    if not identifier:
        return None
    found = []
    for company in Company.objects.filter(is_deleted=False, is_active=True).exclude(db_name=None):
        try:
            alias = register_tenant_db(company.db_name)
            staff = Staffcreation.objects.using(alias).filter(is_deleted=False).filter(
                Q(username__iexact=identifier) | Q(emp_id__iexact=identifier))
            leaders = (
                PanchayatLeaderLogin.objects.using(alias).filter(username__iexact=identifier).exists()
                or DistrictLeaderLogin.objects.using(alias).filter(username__iexact=identifier).exists()
            )
            if staff.exists() or leaders:
                found.append(company)
        except Exception:  # one broken company DB must not block logins elsewhere
            logger.exception("Login lookup failed for %s", company.db_name)
    return found[0] if len(found) == 1 else None


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        cookie_token = None
        try:
            path = request.path_info
            match = TENANT_URL.match(path)
            if match:
                slug = match["slug"]
                cookie_token = set_cookie_scope(company_cookie_name(slug), f"/api/t/{slug}/")
                return self._company_url(request, slug, match["rest"])
            if path.startswith(SUPER_PREFIX):
                cookie_token = set_cookie_scope("iwms_super", SUPER_PREFIX)
                return self.get_response(request)
            if path.startswith(LEGACY_PREFIXES):
                return self._legacy_url(request)
            return self.get_response(request)
        finally:
            if cookie_token is not None:
                reset_cookie_scope(cookie_token)

    def _company_url(self, request, slug, rest):
        company = _company(slug=slug)
        if company is None:
            return _deny(404, "Company not found.")
        if not company.is_active and rest != "ws/info/":
            return _deny(403, "This company account is disabled. Contact the administrator.")

        new_path = "/api/v1/" + rest
        request.path_info = new_path
        request.path = request.META.get("SCRIPT_NAME", "").rstrip("/") + new_path
        request.META["PATH_INFO"] = new_path
        request.tenant_url_slug = slug
        request.company = company

        payload = _payload(request)
        if payload and not is_super_payload(payload):
            token_slug = payload.get("company")
            token_company = payload.get("company_unique_id")
            if (token_slug and token_slug != slug) or (
                not token_slug and token_company and token_company != company.unique_id
            ):
                return _deny(401, "This login is not valid for this company.")
        request.token_payload = payload
        with tenant_context(register_tenant_db(company.db_name)):
            return self.get_response(request)

    def _legacy_url(self, request):
        payload = _payload(request)
        request.token_payload = payload
        company = None
        if payload and is_super_payload(payload):
            company = _company_named_by_request(request)
        elif payload:
            company = _company(slug=payload["company"]) if payload.get("company") else None
            if company is None and payload.get("company_unique_id"):
                company = _company(unique_id=payload["company_unique_id"])
        elif request.method == "POST" and per_db_mode():
            body = _json_body(request)
            if (body.get("user_type") or body.get("login_type")) != "platform":
                identifier = next((body.get(f) for f in LOGIN_FIELDS if isinstance(body.get(f), str)), None)
                if identifier:
                    company = find_company_for_login(identifier)
        if company is None:
            return self.get_response(request)
        if not company.is_active and not is_super_payload(payload):
            return _deny(403, "This company account is disabled. Contact the administrator.")
        request.company = company
        with tenant_context(register_tenant_db(company.db_name)):
            return self.get_response(request)

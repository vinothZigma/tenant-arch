"""Tokens and authentication for the super admin console and the company
workspace. Tokens are the same HS256 JWTs the rest of IWMS uses (simplejwt
AccessToken + IWMS claims), plus:

    super admin   {scope: "super",  user_type: "platform", unique_id}
    company user  {scope: "tenant", company: <slug>, user_type: "staff", unique_id}
    open workspace: the company token also has imp: <super admin username>

Browsers get them in an HttpOnly cookie scoped to /api/super/ or
/api/t/<slug>/ (see TenantMiddleware); "open workspace" tokens are returned
in the body and kept in that tab's sessionStorage.
"""
from django.contrib.auth import get_user_model
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission
from rest_framework_simplejwt.tokens import AccessToken

from app.authentication.jwt import JWTUserAuthentication
from app.models.superadmin.staff_management.staffcreation import Staffcreation


def make_token(user, **claims):
    access = AccessToken.for_user(user)
    for key, value in claims.items():
        access[key] = value
    return str(access), access["exp"] - access["iat"]


def super_token(user):
    return make_token(
        user, unique_id=user.unique_id, user_type="platform", scope="super", role="superadmin",
        name=user.get_full_name() if hasattr(user, "get_full_name") else user.username,
        email=user.email or "",
    )


def tenant_token(staff, company, imp=None):
    claims = dict(
        unique_id=staff.staff_unique_id, user_type="staff", scope="tenant", company=company.slug,
        company_unique_id=company.unique_id, company_name=company.name,
        role="admin" if staff.is_company_admin else "staff", name=staff.employee_name,
        company_admin=bool(staff.is_company_admin),
        email=staff.office_email or "",
    )
    if imp:
        claims["imp"] = imp
    return make_token(staff, **claims)


class SuperAdminAuthentication(BaseAuthentication):
    """A platform super admin (master auth_user, is_superuser) token."""

    def authenticate_header(self, request):
        return "Bearer"

    def authenticate(self, request):
        result = JWTUserAuthentication().authenticate(request)
        if not result:
            return None
        user, _ = result
        payload = getattr(request, "jwt_payload", None) or {}
        if not isinstance(user, get_user_model()) or not user.is_superuser or not user.is_active:
            raise AuthenticationFailed("Not a super admin login.")
        if payload.get("scope") not in (None, "super"):
            raise AuthenticationFailed("Not a super admin login.")
        return user, payload


class CompanyUserAuthentication(BaseAuthentication):
    """A staff login of the company in the URL (TenantMiddleware already
    refused tokens of other companies)."""

    def authenticate_header(self, request):
        return "Bearer"

    def authenticate(self, request):
        result = JWTUserAuthentication().authenticate(request)
        if not result:
            return None
        user, _ = result
        payload = getattr(request, "jwt_payload", None) or {}
        company = getattr(request, "company", None)
        if company is None:
            raise AuthenticationFailed("Open this from the company's own address (/<company>/...).")
        if not isinstance(user, Staffcreation):
            raise AuthenticationFailed("Sign in with a company login.")
        if payload.get("company") not in (None, company.slug):
            raise AuthenticationFailed("This login is not valid for this company.")
        if user.is_deleted or not user.login_enabled:
            raise AuthenticationFailed("Your login is switched off.")
        return user, payload


class IsCompanyAdmin(BasePermission):
    message = "Only company admins can do this."

    def has_permission(self, request, view):
        return bool(getattr(request.user, "is_company_admin", False))

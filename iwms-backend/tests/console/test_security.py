"""Security tests: authentication, sessions, access control between companies,
injection, data exposure, headers / CORS and brute force.

Each test is written as an attack: the input data is what an attacker sends,
the expected result is how a safe system answers. A failing test here is a
security finding to fix in the app, not a test to loosen.
"""
import base64
import json
import time

import jwt
import pytest
from django.conf import settings
from rest_framework.test import APIClient

from app.models.superadmin_masters.company import Company
from app.models.tenancy.master import Plan

from .conftest import ROOT_PASSWORD, ROOT_USER, bearer, company_client, company_login, create_company

pytestmark = [pytest.mark.security, pytest.mark.django_db(transaction=True)]

HASH_MARKERS = ("pbkdf2_", "md5$", "argon2", "bcrypt", "sha1$")


def super_login(username=ROOT_USER, password=ROOT_PASSWORD, portal=None):
    c = APIClient()
    headers = {"HTTP_X_IWMS_PORTAL": portal} if portal else {}
    return c, c.post("/api/super/login/", {"username": username, "password": password}, format="json", **headers)


def b64(data):
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def is_refused(status):
    return status in (401, 403)


# ---------------------------------------------------------------- authentication

@pytest.mark.case("Token with a changed signature is refused", data={
    "attack": "take a real super admin token and change its last character",
    "endpoint": "GET /api/super/me/",
})
def test_tampered_token(case, platform):
    _, r = super_login()
    token = r.data["token"]
    forged = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
    case.check("status with the real token", 200, bearer(token).get("/api/super/me/").status_code)
    status = bearer(forged).get("/api/super/me/").status_code
    case.check("status with the tampered token", "401/403", status, ok=is_refused(status))


@pytest.mark.case("Token signed with another secret key is refused", data={
    "attack": "copy the token's claims and sign them with 'attacker-secret'",
    "endpoint": "GET /api/super/companies/",
})
def test_wrong_secret(case, platform):
    _, r = super_login()
    claims = jwt.decode(r.data["token"], settings.SECRET_KEY, algorithms=["HS256"])
    forged = jwt.encode(claims, "attacker-secret", algorithm="HS256")
    status = bearer(forged).get("/api/super/companies/").status_code
    case.check("status", "401/403", status, ok=is_refused(status))


@pytest.mark.case("Unsigned token (alg: none) is refused", data={
    "attack": 'header {"alg": "none"} + real claims + empty signature',
    "endpoint": "GET /api/super/companies/",
})
def test_alg_none(case, platform):
    _, r = super_login()
    claims = jwt.decode(r.data["token"], settings.SECRET_KEY, algorithms=["HS256"])
    forged = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(claims)}."
    status = bearer(forged).get("/api/super/companies/").status_code
    case.check("status", "401/403", status, ok=is_refused(status))


@pytest.mark.case("Expired token is refused", data={
    "attack": "real claims with exp set 1 hour in the past, signed with the real key",
    "endpoint": "GET /api/super/me/",
})
def test_expired_token(case, platform):
    _, r = super_login()
    claims = jwt.decode(r.data["token"], settings.SECRET_KEY, algorithms=["HS256"])
    claims["exp"] = int(time.time()) - 3600
    expired = jwt.encode(claims, settings.SECRET_KEY, algorithm="HS256")
    status = bearer(expired).get("/api/super/me/").status_code
    case.check("status", "401/403", status, ok=is_refused(status))


@pytest.mark.case("Login does not reveal whether a username exists", data={
    "attempt 1": f"username={ROOT_USER}, password=wrong-pass",
    "attempt 2": "username=no-such-user, password=wrong-pass",
})
def test_no_user_enumeration(case, platform):
    _, known = super_login(ROOT_USER, "wrong-pass")
    _, unknown = super_login("no-such-user", "wrong-pass")
    case.check("same status code", known.status_code, unknown.status_code)
    case.check("same message", str(known.data), str(unknown.data))


@pytest.mark.parametrize("kind", ["normal user", "disabled super admin"])
@pytest.mark.case("Only active super admins can sign in to the console")
def test_console_login_only_super_admins(case, platform, kind):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    if kind == "normal user":
        clerk = User.objects.create_superuser(username="clerk", password="clerkpass1")
        User.objects.filter(pk=clerk.pk).update(is_superuser=False, is_staff=False)
        username, password = "clerk", "clerkpass1"
    else:
        User.objects.create_superuser(username="old_admin", password="oldpass12", is_active=False)
        username, password = "old_admin", "oldpass12"
    case.given(account=kind, username=username, password=password, endpoint="POST /api/super/login/")
    _, r = super_login(username, password)
    case.check("status", 400, r.status_code)
    case.check("no token given", False, "token" in (r.data or {}))


@pytest.mark.parametrize("password", ["123", "abcde", ""])
@pytest.mark.case("Weak admin password is refused when creating a company")
def test_weak_password_refused(case, sup, password):
    case.given(endpoint="POST /api/super/companies/", company="Weak Co", admin="boss", password=password)
    r = sup.post("/api/super/companies/", {
        "name": "Weak Co", "plan": Plan.objects.get(code="basic").pk,
        "admins": [{"username": "boss", "password": password}],
        "projects": [{"name": "P", "modules": [{"code": "assets"}], "admin_usernames": ["boss"]}],
    }, format="json")
    case.check("status", 400, r.status_code)
    case.check("company not created", False, Company.objects.filter(name="Weak Co").exists())


# ---------------------------------------------------------------- brute force

@pytest.mark.case("Repeated wrong passwords get the account locked or slowed down", data={
    "attack": "12 wrong passwords in a row for the super admin, then the right one",
    "endpoint": "POST /api/super/login/",
})
def test_console_brute_force(case, platform):
    """After many failures a safe login answers 429 / locks the account, so a
    password cannot be guessed by trying thousands of them."""
    statuses = [super_login(ROOT_USER, f"guess-{i}")[1].status_code for i in range(12)]
    case.metric(wrong_attempts=12, statuses=sorted(set(statuses)))
    blocked = any(s == 429 for s in statuses)
    _, last = super_login()
    blocked = blocked or last.status_code in (403, 423, 429)
    case.check("login throttled or locked after 12 failures", "throttled / locked",
               "throttled / locked" if blocked else "no limit: every attempt was answered normally", ok=blocked)


@pytest.mark.case("Company login is slowed down after repeated wrong passwords", data={
    "attack": "12 wrong passwords for company admin 'boss'",
    "endpoint": "POST /api/t/<company>/ws/auth/login/",
})
def test_company_brute_force(case, sup):
    c = create_company(sup, "Brute Co")
    statuses = [company_login(c.slug, "boss", f"guess-{i}").status_code for i in range(12)]
    case.metric(wrong_attempts=12, statuses=sorted(set(statuses)))
    blocked = any(s == 429 for s in statuses) or company_login(c.slug, "boss").status_code in (403, 423, 429)
    case.check("login throttled or locked after 12 failures", "throttled / locked",
               "throttled / locked" if blocked else "no limit: every attempt was answered normally", ok=blocked)


# ---------------------------------------------------------------- session cookie

@pytest.mark.case("Browser login keeps the token in an HttpOnly cookie", data={
    "endpoint": "POST /api/super/login/", "header": "X-IWMS-Portal: admin",
})
def test_cookie_flags(case, platform):
    _, r = super_login(portal="admin")
    case.check("status", 200, r.status_code)
    case.check("token not in the response body", False, "token" in r.data)
    cookie = next(iter(r.cookies.values()), None)
    case.check("auth cookie set", True, cookie is not None)
    case.check("HttpOnly (JavaScript can't read it)", True, bool(cookie["httponly"]))
    case.check("SameSite", True, cookie["samesite"] in ("Lax", "Strict"), ok=cookie["samesite"] in ("Lax", "Strict"))
    case.check("scoped to /api/super/", "/api/super/", cookie["path"])


@pytest.mark.case("Auth cookie is ignored without the portal header (CSRF guard)", data={
    "attack": "cross-site request that carries the cookie but cannot add X-IWMS-Portal",
    "endpoint": "GET /api/super/me/",
})
def test_cookie_needs_portal_header(case, platform):
    client, r = super_login(portal="admin")
    case.check("with header", 200, client.get("/api/super/me/", HTTP_X_IWMS_PORTAL="admin").status_code)
    status = client.get("/api/super/me/").status_code
    case.check("same cookie, no header", "401/403", status, ok=is_refused(status))


@pytest.mark.case("Logout removes the auth cookie", data={"endpoint": "POST /api/super/logout/"})
def test_logout_clears_cookie(case, platform):
    client, _ = super_login(portal="admin")
    r = client.post("/api/super/logout/", HTTP_X_IWMS_PORTAL="admin")
    cookie = next(iter(r.cookies.values()), None)
    case.check("cookie cleared", True, cookie is not None and (cookie.value == "" or cookie["max-age"] in (0, "0")))
    status = client.get("/api/super/me/", HTTP_X_IWMS_PORTAL="admin").status_code
    case.check("signed out afterwards", "401/403", status, ok=is_refused(status))


# ---------------------------------------------------------------- access control

@pytest.mark.case("Company A's token cannot read company B", data={
    "company A": "Alpha (admin alice)", "company B": "Bravo (admin bob)",
    "attack": "alice's token on /api/t/bravo/ws/me/ and B's records",
})
def test_cross_company(case, sup):
    a = create_company(sup, "Alpha", admin="alice")
    b = create_company(sup, "Bravo", admin="bob")
    api_a = company_client(a, "alice")
    pid_b = company_client(b, "bob").get(f"/api/t/{b.slug}/ws/me/").data["projects"][0]["id"]
    s1 = api_a.get(f"/api/t/{b.slug}/ws/me/").status_code
    s2 = api_a.get(f"/api/t/{b.slug}/ws/projects/{pid_b}/modules/assets/records/").status_code
    case.check("B's profile", "401/403", s1, ok=is_refused(s1))
    case.check("B's records", "401/403", s2, ok=is_refused(s2))


@pytest.mark.parametrize("path, method", [
    ("/api/super/companies/", "get"), ("/api/super/plans/", "post"), ("/api/super/testing/runs/", "post"),
])
@pytest.mark.case("Company admin token cannot use the console API")
def test_company_token_on_console(case, sup, path, method):
    c = create_company(sup, "Tenant Co")
    token = company_login(c.slug, "boss").data["token"]
    case.given(attack="company admin token on the super admin API", endpoint=f"{method.upper()} {path}")
    status = getattr(bearer(token), method)(path, {}, format="json").status_code
    case.check("status", "401/403", status, ok=is_refused(status))


# ---------------------------------------------------------------- injection

@pytest.mark.parametrize("username", ["' OR '1'='1", "root'--", "admin\" OR \"\"=\"", "%"])
@pytest.mark.case("SQL injection in the login form does not sign in")
def test_sql_injection_login(case, platform, username):
    case.given(endpoint="POST /api/super/login/", username=username, password="' OR '1'='1")
    _, r = super_login(username, "' OR '1'='1")
    case.check("status", 400, r.status_code)
    case.check("no token", False, "token" in (r.data or {}))


@pytest.mark.parametrize("payload", [
    "Robert'); DROP TABLE module_assets;--", "<script>alert('xss')</script>", "{{7*7}}", "../../etc/passwd",
])
@pytest.mark.case("Attack text is stored as plain text, never run")
def test_injection_payload_stored_as_text(case, sup, payload):
    """Saved as a record name, the text must come back exactly as sent and the
    table must still work (no SQL ran), in a JSON response (no HTML to run)."""
    c = create_company(sup, "Inject Co")
    api = company_client(c)
    pid = api.get(f"/api/t/{c.slug}/ws/me/").data["projects"][0]["id"]
    url = f"/api/t/{c.slug}/ws/projects/{pid}/modules/assets/records/"
    case.given(endpoint=f"POST {url}", name=payload)
    r = api.post(url, {"data": {"name": payload}}, format="json")
    case.check("saved", 201, r.status_code)
    rows = api.get(url)
    case.check("list still works", 200, rows.status_code)
    case.check("text unchanged", payload, rows.data[0]["data"]["name"])
    case.check("response is JSON", True, rows["Content-Type"].startswith("application/json"))


@pytest.mark.parametrize("name", ["x; DROP DATABASE iwms", "A`B'C\"D", "../../../tmp/evil"])
@pytest.mark.case("Company name cannot inject into its database name")
def test_company_name_injection(case, sup, name):
    import re

    case.given(endpoint="POST /api/super/companies/", company_name=name)
    c = create_company(sup, name, admin="boss")
    case.check("database name only a-z 0-9 _", True, bool(re.fullmatch(r"[a-z0-9_]{1,64}", c.db_name)))
    case.metric(db_name=c.db_name, slug=c.slug)


@pytest.mark.parametrize("path", [
    "/api/super/testing/runs/..%2F..%2Fetc/", "/api/super/testing/runs/x/screens/..%2F..%2Fmeta.json/",
    "/api/super/testing/runs/.hidden/log/",
])
@pytest.mark.case("Path traversal on the testing files is refused")
def test_path_traversal(case, sup, path):
    case.given(attack="read files outside the run folder", endpoint=f"GET {path}")
    status = sup.get(path).status_code
    case.check("status", "404/400", status, ok=status in (400, 404))


# ---------------------------------------------------------------- data exposure

@pytest.mark.case("Password hashes never appear in API responses", data={
    "endpoints": "GET /api/super/me/, /api/super/companies/, /api/t/<company>/ws/me/, …/users/",
})
def test_no_password_hashes(case, sup):
    c = create_company(sup, "Leak Co")
    api = company_client(c)
    for label, resp in [
        ("super me", sup.get("/api/super/me/")), ("companies", sup.get("/api/super/companies/")),
        ("company me", api.get(f"/api/t/{c.slug}/ws/me/")), ("company users", api.get(f"/api/t/{c.slug}/ws/users/")),
    ]:
        body = resp.content.decode(errors="replace")
        leaked = [m for m in HASH_MARKERS if m in body]
        case.check(f"{label}: no password hash", [], leaked)


@pytest.mark.case("Errors don't show a traceback or settings", data={
    "attack": "malformed JSON and an unknown URL", "endpoints": "POST /api/super/login/, GET /api/super/nope/",
})
def test_no_debug_leak(case, platform):
    r1 = APIClient().post("/api/super/login/", "{not json", content_type="application/json")
    r2 = APIClient().get("/api/super/nope/")
    for label, r in [("bad JSON", r1), ("unknown URL", r2)]:
        body = r.content.decode(errors="replace")
        case.check(f"{label}: no traceback", False, "Traceback" in body or "SECRET_KEY" in body)
        case.check(f"{label}: not a server error", True, r.status_code < 500, ok=r.status_code < 500)


# ---------------------------------------------------------------- headers / CORS

@pytest.mark.case("Security headers are sent", data={"endpoint": "GET /api/super/me/"})
def test_security_headers(case, sup):
    r = sup.get("/api/super/me/")
    case.check("X-Frame-Options (no clickjacking)", True, r.get("X-Frame-Options") in ("DENY", "SAMEORIGIN"),
               ok=r.get("X-Frame-Options") in ("DENY", "SAMEORIGIN"))
    case.check("X-Content-Type-Options", "nosniff", r.get("X-Content-Type-Options"))


@pytest.mark.parametrize("origin, allowed", [
    ("https://evil.example.com", False), ("http://localhost:5173", True),
])
@pytest.mark.case("CORS lets only known sites call the API with cookies")
def test_cors(case, platform, origin, allowed):
    case.given(origin=origin, request="OPTIONS /api/super/me/ (browser pre-flight)")
    r = APIClient().options("/api/super/me/", HTTP_ORIGIN=origin, HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET")
    got = r.get("Access-Control-Allow-Origin") == origin
    case.check("origin allowed", allowed, got)

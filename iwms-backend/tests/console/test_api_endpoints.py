"""API tests: status codes, bodies and messages of the console and company APIs."""
import pytest
from rest_framework.test import APIClient

from app.models.tenancy.master import Plan

from .conftest import ROOT_PASSWORD, ROOT_USER, bearer, company_login, create_company

pytestmark = [pytest.mark.api, pytest.mark.django_db(transaction=True)]


@pytest.mark.case("Super admin login with the right password", data={
    "endpoint": "POST /api/super/login/", "username": ROOT_USER, "password": ROOT_PASSWORD,
})
def test_super_login_ok(case, platform):
    r = APIClient().post("/api/super/login/", {"username": ROOT_USER, "password": ROOT_PASSWORD}, format="json")
    case.check("status code", 200, r.status_code)
    case.check("token returned", True, bool(r.data.get("token")))


@pytest.mark.parametrize("username, password", [
    (ROOT_USER, "wrong-pass"), ("nobody", "whatever1"), ("", ""),
])
@pytest.mark.case("Super admin login is refused with bad credentials")
def test_super_login_refused(case, platform, username, password):
    case.given(endpoint="POST /api/super/login/", username=username, password=password)
    r = APIClient().post("/api/super/login/", {"username": username, "password": password}, format="json")
    case.check("status code is 400 or 401", True, r.status_code in (400, 401), ok=r.status_code in (400, 401))
    case.check("no token in the body", False, "token" in (r.data or {}))


@pytest.mark.parametrize("path", [
    "/api/super/companies/", "/api/super/plans/", "/api/super/me/", "/api/super/testing/",
])
@pytest.mark.case("Console API needs a super admin sign-in")
def test_super_api_needs_auth(case, platform, path):
    case.given(endpoint=f"GET {path}", auth="none")
    status = APIClient().get(path).status_code
    case.check("status code is 401 or 403", "401/403", status, ok=status in (401, 403))


@pytest.mark.case("Plans list returns the seeded plans", data={"endpoint": "GET /api/super/plans/"})
def test_plans_list(case, sup):
    r = sup.get("/api/super/plans/")
    case.check("status code", 200, r.status_code)
    case.check("plan codes", sorted(Plan.objects.values_list("code", flat=True)), sorted(p["code"] for p in r.data))


@pytest.mark.case("Reserved admin username is refused with a readable message", data={
    "endpoint": "POST /api/super/companies/", "name": "X", "admin username": "developer",
})
def test_company_reserved_username(case, sup):
    r = sup.post("/api/super/companies/", {
        "name": "X", "plan": Plan.objects.get(code="basic").pk,
        "admins": [{"username": "developer", "password": "secret12"}],
        "projects": [{"name": "P", "modules": [{"code": "assets"}], "admin_usernames": ["developer"]}],
    }, format="json")
    case.check("status code", 400, r.status_code)
    case.check("message mentions 'reserved'", True, "reserved" in str(r.data))


@pytest.mark.case("Company token is refused on the console API", data={
    "company": "Acme", "login": "boss", "endpoint": "GET /api/super/companies/",
})
def test_company_token_refused_on_super_api(case, sup):
    c = create_company(sup, "Acme")
    token = company_login(c.slug, "boss").data["token"]
    status = bearer(token).get("/api/super/companies/").status_code
    case.check("status code is 401 or 403", "401/403", status, ok=status in (401, 403))


@pytest.mark.parametrize("username, password, expected", [
    ("boss", "secret12", 200), ("boss", "wrong-pass", 400), ("ghost", "secret12", 400),
])
@pytest.mark.case("Company admin login")
def test_company_login(case, sup, username, password, expected):
    c = create_company(sup, "Kilo")
    case.given(endpoint=f"POST /api/t/{c.slug}/ws/auth/login/", username=username, password=password)
    status = company_login(c.slug, username, password).status_code
    if expected == 200:
        case.check("status code", 200, status)
    else:
        case.check("status code is 400 or 401", "400/401", status, ok=status in (400, 401))


@pytest.fixture
def testing_dir(monkeypatch, tmp_path):
    """Console → Testing writes into a temp folder with a ready test list."""
    import json

    from app.testing import runner

    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(runner, "LAST", tmp_path / "last_results.json")
    monkeypatch.setattr(runner, "CATALOG", tmp_path / "catalog.json")
    (tmp_path / "catalog.json").write_text(json.dumps({"mtime": 9e12, "collection_errors": [], "cases": [
        {"id": "tests/console/test_unit_rules.py::test_x", "type": "unit", "area": "Rules", "title": "Example",
         "description": "", "file": "tests/console/test_unit_rules.py", "data": {"name": "Pump"}},
    ]}))
    return tmp_path


@pytest.mark.case("Testing API lists the eight test types and the test count", data={
    "endpoint": "GET /api/super/testing/",
})
def test_testing_home(case, sup, testing_dir):
    r = sup.get("/api/super/testing/")
    case.check("status code", 200, r.status_code)
    case.check("types", ["unit", "integration", "api", "ui", "functional", "security", "regression", "performance"],
               [t["key"] for t in r.data["types"]])
    case.check("unit test count", 1, r.data["counts"]["unit"])
    case.check("no active run", None, r.data["active"])


@pytest.mark.parametrize("body, status", [
    ({"type": "nonsense"}, 400), ({"type": "selected", "tests": []}, 400),
    ({"type": "selected", "tests": ["/etc/passwd"]}, 400),
])
@pytest.mark.case("Testing API refuses a bad run request")
def test_testing_bad_run(case, sup, testing_dir, body, status):
    case.given(endpoint="POST /api/super/testing/runs/")
    r = sup.post("/api/super/testing/runs/", body, format="json")
    case.check("status code", status, r.status_code)
    case.check("nothing started", [], sup.get("/api/super/testing/runs/").data)


@pytest.mark.parametrize("run_id", ["../../etc", "nope", ".hidden"])
@pytest.mark.case("Testing API refuses unknown or unsafe run ids")
def test_testing_unknown_run(case, sup, testing_dir, run_id):
    case.given(endpoint=f"GET /api/super/testing/runs/{run_id}/")
    case.check("status code", 404, sup.get(f"/api/super/testing/runs/{run_id}/").status_code)

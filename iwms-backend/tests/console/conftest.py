"""Fixtures for the Console → Testing suite (tests/console/).

Every test here carries a type marker (unit, integration, api, ui,
functional, performance) and @pytest.mark.case(title, data=...) so the
console can show a readable title and the input data before it runs.
The `case` fixture (app/testing/pytest_plugin.py) records checks.

Company databases are SQLite files in a temp folder (tenant_dirs);
the master DB is in-memory SQLite (config.test_settings).
"""
import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from app.models.superadmin_masters.company import Company
from app.models.tenancy.master import Plan
from app.tenancy import enforcement
from tests.tenancy.conftest import tenant_dirs  # noqa: F401  (re-exported fixture)

ROOT_USER, ROOT_PASSWORD = "root", "rootpass1"
ADMIN_PASSWORD = "secret12"
DEFAULT_MODULES = [
    {"code": "assets", "can_create": True, "can_edit": True, "can_delete": True},
    {"code": "staff-creations", "can_create": True, "can_edit": True, "can_delete": True},
]

# `case` fixture also when pytest runs without -p app.testing.pytest_plugin.
from app.testing.pytest_plugin import case  # noqa: E402,F401


@pytest.fixture
def platform(settings, tenant_dirs, tmp_path):  # noqa: F811
    """Per-company DB mode, default plans/modules seeded, a platform super admin."""
    settings.TENANCY_MODE = "per_db"
    settings.DEVELOPER_PASSWORD = "devpass123"
    settings.MEDIA_ROOT = str(tmp_path / "media")
    enforcement.clear_cache()
    with open("/dev/null", "w") as quiet:
        call_command("seed_defaults", stdout=quiet)
    from django.contrib.auth import get_user_model

    get_user_model().objects.create_superuser(username=ROOT_USER, password=ROOT_PASSWORD)
    yield
    enforcement.clear_cache()


def bearer(token):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return c


@pytest.fixture
def sup(platform):
    """API client signed in as the platform super admin."""
    r = APIClient().post("/api/super/login/", {"username": ROOT_USER, "password": ROOT_PASSWORD}, format="json")
    assert r.status_code == 200, r.content
    return bearer(r.data["token"])


def create_company(sup, name, plan="basic", admin="boss", modules=None, project="North"):
    r = sup.post("/api/super/companies/", {
        "name": name, "plan": Plan.objects.get(code=plan).pk, "contact_email": "ops@example.com",
        "theme_color": "#112233",
        "admins": [{"username": admin, "password": ADMIN_PASSWORD, "full_name": f"{name} Admin"}],
        "projects": [{"name": project, "description": "", "modules": modules or DEFAULT_MODULES,
                      "admin_usernames": [admin]}],
    }, format="json")
    assert r.status_code == 201, r.content
    return Company.objects.get(pk=r.data["id"])


def company_login(slug, username, password=ADMIN_PASSWORD):
    return APIClient().post(f"/api/t/{slug}/ws/auth/login/", {"username": username, "password": password},
                            format="json")


def company_client(company, username="boss"):
    r = company_login(company.slug, username)
    assert r.status_code == 200, r.content
    return bearer(r.data["token"])

"""Integration tests: master DB + company databases + seeders working together."""
import pytest

from app.models.superadmin.staff_management.staffcreation import Staffcreation
from app.models.tenancy.master import Module, Plan
from app.tenancy.db import register_tenant_db, sqlite_path, tenant_table_names

from .conftest import company_client, create_company

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]


@pytest.mark.case("Seeder creates the default plans and modules", data={"command": "manage.py seed_defaults"})
def test_seed_defaults(case, platform):
    """The master DB gets the three plans and every built-in IWMS module."""
    plans = sorted(Plan.objects.values_list("code", flat=True))
    case.check("plans", ["basic", "industry", "premium"], plans)
    case.check("built-in modules registered", True, Module.objects.filter(kind=Module.BUILTIN).count() >= 19,
               ok=Module.objects.filter(kind=Module.BUILTIN).count() >= 19)
    case.metric(builtin_modules=Module.objects.filter(kind=Module.BUILTIN).count(),
                custom_modules=Module.objects.exclude(kind=Module.BUILTIN).count())


@pytest.mark.case("New company gets its own database", data={
    "company": "Tata Motors", "plan": "basic", "admin": "boss", "project": "North",
})
def test_company_database_created(case, sup):
    """Creating a company makes a separate DB with IWMS tables, the admin and the hidden developer."""
    company = create_company(sup, "Tata Motors")
    case.step("POST /api/super/companies/ → company row in master DB")
    case.check("slug", "tata-motors", company.slug)
    case.check("database name", "iwms_company_tata_motors", company.db_name)
    case.check("database file exists", True, sqlite_path(company.db_name).exists())
    tables = tenant_table_names(company.db_name)
    case.check("staff table in company DB", True, "app_staffcreation" in tables or any("staff" in t for t in tables))
    alias = register_tenant_db(company.db_name)
    logins = set(Staffcreation.objects.using(alias).filter(is_company_admin=True)
                 .values_list("username", "is_developer"))
    case.check("company logins", {("boss", False), ("developer", True)}, logins)
    case.metric(tables_in_company_db=len(tables))


@pytest.mark.case("Company data is refused on the master database", data={
    "company": "Acme", "admin": "boss", "query": "Staffcreation.objects.filter(username='boss')",
})
def test_company_data_needs_a_company_db(case, sup):
    """Without a selected company DB the router raises instead of reading the master DB."""
    from app.tenancy.context import TenantNotSelected

    create_company(sup, "Acme")
    try:
        Staffcreation.objects.filter(username="boss").count()
        outcome = "query ran on master"
    except TenantNotSelected:
        outcome = "refused"
    case.check("query without a company DB", "refused", outcome)
    alias = register_tenant_db("iwms_company_acme")
    case.check("same query on the company DB", 1, Staffcreation.objects.using(alias).filter(username="boss").count())


@pytest.mark.case("Records stay inside their own company", data={
    "company A": "Alpha (admin alice)", "company B": "Bravo (admin bob)", "record": "Pump (assets)",
})
def test_company_isolation(case, sup):
    """A record saved by company A is not visible to company B, and A's token is refused by B."""
    a = create_company(sup, "Alpha", admin="alice")
    b = create_company(sup, "Bravo", admin="bob")
    api_a, api_b = company_client(a, "alice"), company_client(b, "bob")
    pid_a = api_a.get(f"/api/t/{a.slug}/ws/me/").data["projects"][0]["id"]
    pid_b = api_b.get(f"/api/t/{b.slug}/ws/me/").data["projects"][0]["id"]
    r = api_a.post(f"/api/t/{a.slug}/ws/projects/{pid_a}/modules/assets/records/", {"data": {"name": "Pump"}},
                   format="json")
    case.check("A creates record", 201, r.status_code)
    rows_b = api_b.get(f"/api/t/{b.slug}/ws/projects/{pid_b}/modules/assets/records/").data
    case.check("records seen by B", [], rows_b)
    case.check("A's token on B's API", True, api_a.get(f"/api/t/{b.slug}/ws/me/").status_code in (401, 403),
               ok=api_a.get(f"/api/t/{b.slug}/ws/me/").status_code in (401, 403))

"""Functional tests: whole business flows, the way people use the product."""
import pytest

from app.models.tenancy.master import Plan, PlanModuleLimit

from .conftest import company_client, company_login, create_company

pytestmark = [pytest.mark.functional, pytest.mark.django_db(transaction=True)]


@pytest.mark.case("Onboard a company and its admin starts work", data={
    "company": "Hotel Bravo", "plan": "basic", "admin": "boss / secret12", "project": "North",
    "record": "assets: Pump, cost 1200",
})
def test_onboarding_flow(case, sup):
    case.step("Super admin creates the company with one project and one admin")
    c = create_company(sup, "Hotel Bravo")
    case.step("Admin signs in to the company workspace")
    api = company_client(c)
    me = api.get(f"/api/t/{c.slug}/ws/me/").data
    case.check("admin sees projects", ["North"], [p["name"] for p in me["projects"]])
    pid = me["projects"][0]["id"]
    case.step("Admin adds an asset record")
    url = f"/api/t/{c.slug}/ws/projects/{pid}/modules/assets/records/"
    r = api.post(url, {"data": {"name": "Pump", "cost": "1200"}}, format="json")
    case.check("record saved", 201, r.status_code)
    case.check("cost stored as number", 1200, r.data["data"]["cost"])
    case.step("Usage counter goes up")
    used = api.get(f"/api/t/{c.slug}/ws/me/").data["usage"]["modules"]["assets"]["used"]
    case.check("assets used", 1, used)


@pytest.mark.case("Plan limit, buy extra records, super admin marks paid", data={
    "plan": "basic", "assets limit": 1, "extra bought": 1, "records tried": "A, B",
})
def test_limit_and_extra_records_flow(case, sup):
    PlanModuleLimit.objects.filter(plan__code="basic", module__code="assets").update(record_limit=1)
    c = create_company(sup, "Lima")
    api = company_client(c)
    pid = api.get(f"/api/t/{c.slug}/ws/me/").data["projects"][0]["id"]
    url = f"/api/t/{c.slug}/ws/projects/{pid}/modules/assets/records/"
    case.step("1st record fits the plan")
    case.check("record A", 201, api.post(url, {"data": {"name": "A"}}, format="json").status_code)
    case.step("2nd record is over the limit")
    r = api.post(url, {"data": {"name": "B"}}, format="json")
    case.check("record B refused", 403, r.status_code)
    case.check("message", True, r.data["detail"].startswith("Assets limit reached"),
               ok=r.data["detail"].startswith("Assets limit reached"))
    case.step("Admin requests 1 extra record (pending until paid)")
    req = api.post(f"/api/t/{c.slug}/ws/extra-records/", {"module_code": "assets", "quantity": 1}, format="json")
    case.check("request status", "pending", req.data["status"])
    case.check("still refused while pending", 403, api.post(url, {"data": {"name": "B"}}, format="json").status_code)
    case.step("Super admin marks it paid")
    case.check("mark paid", 200, sup.post(f"/api/super/extra-records/{req.data['id']}/mark-paid/").status_code)
    case.check("record B now saved", 201, api.post(url, {"data": {"name": "B"}}, format="json").status_code)


@pytest.mark.case("Company asks for a plan upgrade and super admin approves", data={
    "company": "Mike", "from plan": "basic", "to plan": "industry",
})
def test_plan_upgrade_flow(case, sup):
    c = create_company(sup, "Mike")
    api = company_client(c)
    industry = Plan.objects.get(code="industry")
    r = api.post(f"/api/t/{c.slug}/ws/plan-requests/", {"plan": industry.pk, "note": "growing"}, format="json")
    case.check("request created", 201, r.status_code)
    again = api.post(f"/api/t/{c.slug}/ws/plan-requests/", {"plan": industry.pk}, format="json")
    case.check("second request refused", 400, again.status_code)
    case.check("approve", 200, sup.post(f"/api/super/plan-requests/{r.data['id']}/approve/").status_code)
    c.refresh_from_db()
    case.check("company plan", "industry", c.plan.code)


@pytest.mark.case("Disabled company cannot sign in", data={"company": "Sierra", "is_active": False})
def test_disabled_company_flow(case, sup):
    c = create_company(sup, "Sierra")
    case.check("login before", 200, company_login(c.slug, "boss").status_code)
    sup.patch(f"/api/super/companies/{c.pk}/", {"is_active": False}, format="json")
    r = company_login(c.slug, "boss")
    case.check("login after disabling", 403, r.status_code)
    case.check("message mentions 'disabled'", True, "disabled" in r.json()["detail"])

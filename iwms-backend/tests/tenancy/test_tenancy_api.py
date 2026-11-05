"""End-to-end tests of the super admin console API and the company workspace
API in per_db mode (SQLite: master in memory, companies as temp files)."""
import threading

import pytest
from django.core.management import call_command
from django.db import connections
from rest_framework.test import APIClient

from app.models.superadmin.staff_management.staffcreation import Staffcreation
from app.models.superadmin_masters.company import Company
from app.models.tenancy.master import Module, Plan, PlanModuleLimit
from app.tenancy import enforcement
from app.tenancy.context import tenant_context
from app.tenancy.db import register_tenant_db, tenant_table_names

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def env(settings, tenant_dirs, tmp_path):
    settings.TENANCY_MODE = "per_db"
    settings.DEVELOPER_PASSWORD = "devpass123"
    settings.MEDIA_ROOT = str(tmp_path / "media")
    enforcement.clear_cache()
    call_command("seed_defaults", stdout=open("/dev/null", "w"))
    from django.contrib.auth import get_user_model

    get_user_model().objects.create_superuser(username="root", password="rootpass1")
    yield
    enforcement.clear_cache()


def bearer(token):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return c


@pytest.fixture
def sup(env):
    r = APIClient().post("/api/super/login/", {"username": "root", "password": "rootpass1"}, format="json")
    assert r.status_code == 200, r.content
    return bearer(r.data["token"])


def make_company(sup, name, plan="basic", modules=None, admin="boss"):
    plan_id = Plan.objects.get(code=plan).pk
    modules = modules or [
        {"code": "assets", "can_create": True, "can_edit": True, "can_delete": False},
        {"code": "staff-creations", "can_create": True, "can_edit": True, "can_delete": True},
        {"code": "transport-masters", "can_create": True, "can_edit": True, "can_delete": True},
    ]
    r = sup.post("/api/super/companies/", {
        "name": name, "plan": plan_id, "contact_email": "ops@example.com", "theme_color": "#112233",
        "admins": [{"username": admin, "password": "secret12", "full_name": f"{name} Admin"}],
        "projects": [{"name": "North", "description": "", "modules": modules, "admin_usernames": [admin]}],
    }, format="json")
    assert r.status_code == 201, r.content
    return Company.objects.get(pk=r.data["id"])


def company_login(slug, username, password="secret12"):
    r = APIClient().post(f"/api/t/{slug}/ws/auth/login/", {"username": username, "password": password},
                         format="json")
    return r


def admin_client(company, username="boss"):
    r = company_login(company.slug, username)
    assert r.status_code == 200, r.content
    return bearer(r.data["token"])


def add_employee(company, project_id, name="Ravi"):
    alias = register_tenant_db(company.db_name)
    with tenant_context(alias):
        s = Staffcreation(employee_name=name, company_id=company.unique_id, project_id=project_id)
        s.save()
    return s


# ── super admin + wizard ───────────────────────────────────────────

class TestWizard:
    def test_creates_db_projects_admin_developer_and_module_table(self, sup):
        company = make_company(sup, "Tata Motors")
        assert company.slug == "tata-motors"
        tables = tenant_table_names(company.db_name)
        assert "module_assets" in tables          # custom module -> real table
        assert "module_staff" not in tables       # built-in modules use IWMS tables
        alias = register_tenant_db(company.db_name)
        admins = Staffcreation.objects.using(alias).filter(is_company_admin=True)
        assert set(admins.values_list("username", "is_developer")) == {("boss", False), ("developer", True)}

        r = sup.get(f"/api/super/companies/{company.pk}/")
        assert r.data["admin_usernames"] == ["boss"]          # developer hidden
        assert r.data["usage"]["users_used"] == 1             # developer not counted
        assert r.data["project_count"] == 1

    def test_validation_errors_are_readable(self, sup):
        r = sup.post("/api/super/companies/", {
            "name": "X", "plan": Plan.objects.get(code="basic").pk, "admins": [
                {"username": "developer", "password": "secret12"}],
            "projects": [{"name": "P", "modules": [{"code": "assets"}], "admin_usernames": ["developer"]}],
        }, format="json")
        assert r.status_code == 400 and "reserved" in str(r.data)
        assert not Company.objects.filter(name="X").exists()

    def test_company_token_is_refused_on_super_api(self, sup):
        company = make_company(sup, "Acme")
        token = company_login(company.slug, "boss").data["token"]
        assert bearer(token).get("/api/super/companies/").status_code in (401, 403)


# ── isolation ──────────────────────────────────────────────────────

class TestIsolation:
    def test_token_for_company_a_is_rejected_on_company_b(self, sup):
        a = make_company(sup, "Alpha", admin="alice")
        b = make_company(sup, "Beta", admin="bob")
        token_a = company_login(a.slug, "alice").data["token"]
        assert bearer(token_a).get(f"/api/t/{a.slug}/ws/me/").status_code == 200
        r = bearer(token_a).get(f"/api/t/{b.slug}/ws/me/")
        assert r.status_code == 401
        assert r.json()["detail"] == "This login is not valid for this company."
        # The same username does not exist in the other company's DB.
        assert company_login(b.slug, "alice").status_code == 400

    def test_records_stay_in_their_company(self, sup):
        a = make_company(sup, "Alpha", admin="alice")
        b = make_company(sup, "Beta", admin="bob")
        ca, cb = admin_client(a, "alice"), admin_client(b, "bob")
        pa = ca.get(f"/api/t/{a.slug}/ws/me/").data["projects"][0]["id"]
        pb = cb.get(f"/api/t/{b.slug}/ws/me/").data["projects"][0]["id"]
        assert ca.post(f"/api/t/{a.slug}/ws/projects/{pa}/modules/assets/records/",
                       {"data": {"name": "Drill"}}, format="json").status_code == 201
        assert cb.get(f"/api/t/{b.slug}/ws/projects/{pb}/modules/assets/records/").data == []
        assert len(ca.get(f"/api/t/{a.slug}/ws/projects/{pa}/modules/assets/records/").data) == 1


# ── records ────────────────────────────────────────────────────────

class TestRecords:
    def test_crud_validation_and_soft_delete(self, sup):
        c = make_company(sup, "Gamma", modules=[
            {"code": "assets", "can_create": True, "can_edit": True, "can_delete": True}])
        api = admin_client(c)
        pid = api.get(f"/api/t/{c.slug}/ws/me/").data["projects"][0]["id"]
        url = f"/api/t/{c.slug}/ws/projects/{pid}/modules/assets/records/"
        bad = api.post(url, {"data": {"cost": "abc"}}, format="json")
        assert bad.status_code == 400 and "name" in bad.data and "cost" in bad.data
        row = api.post(url, {"data": {"name": "Pump", "cost": "1200", "junk": 1}}, format="json").data
        assert row["data"]["cost"] == 1200 and "junk" not in row["data"]
        assert row["created_by_id"]
        r = api.put(f"{url}{row['id']}/", {"data": {"name": "Pump 2", "cost": 5}}, format="json")
        assert r.data["data"]["name"] == "Pump 2"
        assert api.delete(f"{url}{row['id']}/").status_code == 204
        assert api.get(url).data == []
        # Soft delete: still visible to the super admin with the deleted filter.
        r = sup.get(f"/api/super/data/{c.pk}/{pid}/assets/?deleted=1")
        assert len(r.data["records"]) == 1 and r.data["records"][0]["deleted_by"] == "boss"
        assert sup.post(f"/api/super/data/{c.pk}/{pid}/assets/{row['id']}/restore/").status_code == 200
        assert len(api.get(url).data) == 1

    def test_project_cap_blocks_admin(self, sup):
        c = make_company(sup, "Delta")   # assets: can_delete False
        api = admin_client(c)
        pid = api.get(f"/api/t/{c.slug}/ws/me/").data["projects"][0]["id"]
        url = f"/api/t/{c.slug}/ws/projects/{pid}/modules/assets/records/"
        row = api.post(url, {"data": {"name": "Saw"}}, format="json").data
        r = api.delete(f"{url}{row['id']}/")
        assert r.status_code == 403 and "delete" in r.data["detail"]


# ── staff and permissions ─────────────────────────────────────────

class TestStaff:
    def test_staff_cannot_exceed_project_caps(self, sup):
        c = make_company(sup, "Echo")    # assets: no delete
        api = admin_client(c)
        pid = api.get(f"/api/t/{c.slug}/ws/me/").data["projects"][0]["id"]
        emp = add_employee(c, pid)
        r = api.post(f"/api/t/{c.slug}/ws/users/", {
            "employee_id": emp.pk, "username": "ravi", "password": "secret12",
            "permissions": {pid: {"assets": {"view": True, "delete": True}}},
        }, format="json")
        assert r.status_code == 400
        assert "North › Assets: this project does not allow delete" in str(r.data)

    def test_staff_login_gets_only_granted_access(self, sup):
        c = make_company(sup, "Foxtrot")
        api = admin_client(c)
        pid = api.get(f"/api/t/{c.slug}/ws/me/").data["projects"][0]["id"]
        emp = add_employee(c, pid)
        r = api.post(f"/api/t/{c.slug}/ws/users/", {
            "employee_id": emp.pk, "username": "ravi", "password": "secret12",
            "permissions": {pid: {"assets": {"create": True}}},
        }, format="json")
        assert r.status_code == 201, r.content
        assert r.data["permissions"][pid]["assets"] == {"view": True, "create": True, "edit": False, "delete": False}
        staff = admin_client(c, "ravi")
        url = f"/api/t/{c.slug}/ws/projects/{pid}/modules/assets/records/"
        row = staff.post(url, {"data": {"name": "Ladder"}}, format="json")
        assert row.status_code == 201
        assert staff.put(f"{url}{row.data['id']}/", {"data": {"name": "x"}}, format="json").status_code == 403
        assert staff.get(f"/api/t/{c.slug}/ws/users/").status_code == 403   # admin-only
        # The readable staff_access_configuration copy is filled in.
        from app.models.tenancy.tenant import ModulePermission

        perm = ModulePermission.objects.using(register_tenant_db(c.db_name)).get()
        assert (perm.username, perm.project_name, perm.module_name, perm.access) == (
            "ravi", "North", "Assets", "View, Create")

    def test_switching_login_off_blocks_it(self, sup):
        c = make_company(sup, "Golf")
        api = admin_client(c)
        pid = api.get(f"/api/t/{c.slug}/ws/me/").data["projects"][0]["id"]
        emp = add_employee(c, pid)
        api.post(f"/api/t/{c.slug}/ws/users/", {"employee_id": emp.pk, "username": "ravi",
                                                "password": "secret12"}, format="json")
        token = company_login(c.slug, "ravi").data["token"]
        api.put(f"/api/t/{c.slug}/ws/users/{emp.pk}/", {"is_active": False}, format="json")
        assert bearer(token).get(f"/api/t/{c.slug}/ws/me/").status_code == 401
        assert company_login(c.slug, "ravi").status_code == 400


# ── limits and billing ────────────────────────────────────────────

class TestLimits:
    def _limit(self, code, n, plan="basic"):
        PlanModuleLimit.objects.filter(plan__code=plan, module__code=code).update(record_limit=n)

    def test_record_limit_messages_and_extra_records(self, sup):
        self._limit("assets", 1)
        c = make_company(sup, "Hotel")
        api = admin_client(c)
        pid = api.get(f"/api/t/{c.slug}/ws/me/").data["projects"][0]["id"]
        url = f"/api/t/{c.slug}/ws/projects/{pid}/modules/assets/records/"
        assert api.post(url, {"data": {"name": "A"}}, format="json").status_code == 201
        r = api.post(url, {"data": {"name": "B"}}, format="json")
        assert r.status_code == 403
        assert r.data["detail"].startswith("Assets limit reached: 1 on the Basic plan")
        assert "₹20 each" in r.data["detail"]
        # Buy 1 more -> pending doesn't count; paid does.
        req = api.post(f"/api/t/{c.slug}/ws/extra-records/", {"module_code": "assets", "quantity": 1}, format="json")
        assert req.status_code == 201 and req.data["status"] == "pending"
        assert api.post(url, {"data": {"name": "B"}}, format="json").status_code == 403
        assert sup.post(f"/api/super/extra-records/{req.data['id']}/mark-paid/").status_code == 200
        assert api.post(url, {"data": {"name": "B"}}, format="json").status_code == 201
        usage = api.get(f"/api/t/{c.slug}/ws/me/").data["usage"]["modules"]["assets"]
        assert (usage["used"], usage["base_limit"], usage["extra"], usage["limit"]) == (2, 1, 1, 2)

    def test_staff_sees_short_message_and_limits_reached(self, sup):
        self._limit("assets", 0)
        c = make_company(sup, "India")
        api = admin_client(c)
        pid = api.get(f"/api/t/{c.slug}/ws/me/").data["projects"][0]["id"]
        emp = add_employee(c, pid)
        api.post(f"/api/t/{c.slug}/ws/users/", {"employee_id": emp.pk, "username": "ravi", "password": "secret12",
                                                "permissions": {pid: {"assets": {"create": True}}}}, format="json")
        staff = admin_client(c, "ravi")
        me = staff.get(f"/api/t/{c.slug}/ws/me/").data
        assert me["usage"] is None and me["limits_reached"] == ["assets"]
        r = staff.post(f"/api/t/{c.slug}/ws/projects/{pid}/modules/assets/records/", {"data": {"name": "A"}},
                       format="json")
        assert r.data["detail"] == "Assets is full for now. Ask your company admin to add more."

    def test_limit_holds_under_parallel_creates(self, sup):
        self._limit("assets", 1)
        c = make_company(sup, "Juliet")
        token = company_login(c.slug, "boss").data["token"]
        pid = bearer(token).get(f"/api/t/{c.slug}/ws/me/").data["projects"][0]["id"]
        url = f"/api/t/{c.slug}/ws/projects/{pid}/modules/assets/records/"
        barrier, codes = threading.Barrier(2), []

        def create(name):
            try:
                barrier.wait()
                codes.append(bearer(token).post(url, {"data": {"name": name}}, format="json").status_code)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=create, args=(n,)) for n in ("A", "B")]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert sorted(codes) == [201, 403]

    def test_user_limit_counts_admins_but_not_developer(self, sup):
        Plan.objects.filter(code="basic").update(user_limit=2)
        c = make_company(sup, "Kilo")
        api = admin_client(c)
        pid = api.get(f"/api/t/{c.slug}/ws/me/").data["projects"][0]["id"]
        e1, e2 = add_employee(c, pid, "One"), add_employee(c, pid, "Two")
        assert api.post(f"/api/t/{c.slug}/ws/users/", {"employee_id": e1.pk, "username": "one",
                                                       "password": "secret12"}, format="json").status_code == 201
        r = api.post(f"/api/t/{c.slug}/ws/users/", {"employee_id": e2.pk, "username": "two",
                                                    "password": "secret12"}, format="json")
        assert r.status_code == 403 and "User limit reached" in r.data["detail"]

    def test_builtin_module_limit_on_iwms_route(self, sup):
        self._limit("transport-masters", 0)
        c = make_company(sup, "Lima")
        token = company_login(c.slug, "boss").data["token"]
        r = bearer(token).post(f"/api/t/{c.slug}/transport-masters/vehicle-creation/",
                               {"vehicle_no": "TN01AB1234"}, format="json")
        assert r.status_code == 403
        assert r.json()["detail"].startswith("Transport Masters limit reached: 0 on the Basic plan")

    def test_limit_applies_to_the_main_route_only(self, sup):
        self._limit("staff-creations", 0)
        c = make_company(sup, "Lima Two")
        token = company_login(c.slug, "boss").data["token"]
        # Staff limit full: a department (same module, not its main route) is not a staff record.
        r = bearer(token).post(f"/api/t/{c.slug}/staff-creations/departments/", {"name": "Ops"}, format="json")
        assert r.status_code != 403 or "limit reached" not in r.json().get("detail", "")
        r = bearer(token).post(f"/api/t/{c.slug}/staff-creations/staffcreation/", {"employee_name": "X"},
                               format="json")
        assert r.status_code == 403 and "Staff Management limit reached" in r.json()["detail"]

    def test_plan_change_request_flow(self, sup):
        c = make_company(sup, "Mike")
        api = admin_client(c)
        high = Plan.objects.get(code="high")
        r = api.post(f"/api/t/{c.slug}/ws/plan-requests/", {"plan": high.pk, "note": "growing"}, format="json")
        assert r.status_code == 201
        again = api.post(f"/api/t/{c.slug}/ws/plan-requests/", {"plan": high.pk}, format="json")
        assert again.status_code == 400 and "pending" in again.data["detail"]
        assert sup.post(f"/api/super/plan-requests/{r.data['id']}/approve/").status_code == 200
        c.refresh_from_db()
        assert c.plan == high


# ── super admin tools ─────────────────────────────────────────────

class TestSuperTools:
    def test_open_workspace_as_admin(self, sup):
        c = make_company(sup, "November")
        r = sup.post(f"/api/super/companies/{c.pk}/open-workspace/")
        assert r.status_code == 200 and r.data["username"] == "boss"
        me = bearer(r.data["token"]).get(f"/api/t/{c.slug}/ws/me/").data
        assert me["viewed_by_super_admin"] == "root" and me["user"]["username"] == "boss"

    def test_data_tree_and_broken_company(self, sup):
        c = make_company(sup, "Oscar")
        Company.objects.create(name="Broken", db_name="iwms_company_missing")  # DB never created
        tree = {t["name"]: t for t in sup.get("/api/super/data/").data}
        assert tree["Oscar"]["error"] is None and tree["Oscar"]["projects"][0]["name"] == "North"
        assert tree["Broken"]["error"]
        companies = sup.get("/api/super/companies/")
        assert companies.status_code == 200 and len(companies.data) == 2

    def test_project_edit_lowers_staff_access_and_delete_frees_name(self, sup):
        c = make_company(sup, "Papa", modules=[
            {"code": "assets", "can_create": True, "can_edit": True, "can_delete": True}])
        api = admin_client(c)
        pid = api.get(f"/api/t/{c.slug}/ws/me/").data["projects"][0]["id"]
        emp = add_employee(c, pid)
        api.post(f"/api/t/{c.slug}/ws/users/", {"employee_id": emp.pk, "username": "ravi", "password": "secret12",
                                                "permissions": {pid: {"assets": {"delete": True}}}}, format="json")
        r = sup.put(f"/api/super/companies/{c.pk}/projects/{pid}/", {
            "name": "North", "modules": [{"code": "assets", "can_create": True, "can_edit": True,
                                          "can_delete": False}], "admin_usernames": ["boss"]}, format="json")
        assert r.status_code == 200, r.content
        perms = api.get(f"/api/t/{c.slug}/ws/users/").data[0]["permissions"][pid]["assets"]
        assert perms["delete"] is False
        assert sup.delete(f"/api/super/companies/{c.pk}/projects/{pid}/").status_code == 200
        r = sup.post(f"/api/super/companies/{c.pk}/projects/", {
            "name": "North", "modules": [{"code": "vendors"}], "admin_usernames": ["boss"]}, format="json")
        assert r.status_code == 201
        assert "module_assets" not in tenant_table_names(c.db_name)   # no project uses it any more
        assert "module_vendors" in tenant_table_names(c.db_name)

    def test_custom_fields_change_columns(self, sup):
        c = make_company(sup, "Quebec", modules=[{"code": "vendors"}])
        r = sup.put(f"/api/super/companies/{c.pk}/module-fields/", {"vendors": [
            {"name": "name", "type": "text", "required": True}, {"name": "rating", "type": "number"}]},
            format="json")
        assert r.status_code == 200, r.content
        conn = connections[register_tenant_db(c.db_name)]
        cols = {col.name for col in conn.introspection.get_table_description(conn.cursor(), "module_vendors")}
        assert "rating" in cols and "gstin" not in cols
        bad = sup.put(f"/api/super/companies/{c.pk}/module-fields/", {"vendors": [{"name": "is_deleted"}]},
                      format="json")
        assert bad.status_code == 400 and "reserved" in str(bad.data)

    def test_logo_upload_checks_real_type(self, sup, tmp_path):
        from django.core.files.uploadedfile import SimpleUploadedFile

        c = make_company(sup, "Romeo")
        svg = SimpleUploadedFile("x.png", b"<svg onload=alert(1)>", content_type="image/png")
        assert sup.post(f"/api/super/companies/{c.pk}/logo/", {"logo": svg}, format="multipart").status_code == 400
        png = SimpleUploadedFile("x.png", b"\x89PNG\r\n\x1a\n" + b"0" * 20, content_type="image/png")
        r = sup.post(f"/api/super/companies/{c.pk}/logo/", {"logo": png}, format="multipart")
        assert r.status_code == 200 and r.data["logo_url"].endswith(".png")
        info = APIClient().get(f"/api/t/{c.slug}/ws/info/").data
        assert info["logo_url"] == r.data["logo_url"] and info["theme_color"] == "#112233"

    def test_disabled_company_cannot_log_in(self, sup):
        c = make_company(sup, "Sierra")
        sup.patch(f"/api/super/companies/{c.pk}/", {"is_active": False}, format="json")
        r = company_login(c.slug, "boss")
        assert r.status_code == 403 and "disabled" in r.json()["detail"]


class TestLegacyLoginForSuperAdmin:
    def test_platform_login_on_iwms_page_points_to_console(self, env):
        url = "/api/v1/login/"
        r = APIClient().post(url, {"username": "root", "password": "rootpass1", "client": "web"}, format="json")
        assert r.status_code == 400
        assert "use_super_console" in str(r.data)
        # Wrong password: the ordinary message (no hint that the account exists).
        r = APIClient().post(url, {"username": "root", "password": "nope", "client": "web"}, format="json")
        assert r.status_code == 400 and "use_super_console" not in str(r.data)


# ── all IWMS modules ──────────────────────────────────────────────

class TestIwmsModules:
    def test_every_catalog_module_is_registered(self, env):
        from app.utils import permission_catalog as catalog

        builtin = set(Module.objects.filter(kind=Module.BUILTIN).values_list("code", flat=True))
        assert builtin == set(catalog.MODULES)
        assert Module.objects.get(code="transport-masters").api_paths[0] == "transport-masters/vehicle-creation/"

    def test_module_without_records_can_be_used(self, sup):
        c = make_company(sup, "Uniform", modules=[{"code": "dashboard"}, {"code": "reports"}, {"code": "assets"}])
        api = admin_client(c)
        me = api.get(f"/api/t/{c.slug}/ws/me/").data
        assert {m["code"] for m in me["projects"][0]["modules"]} == {"dashboard", "reports", "assets"}
        pid = me["projects"][0]["id"]
        assert api.get(f"/api/t/{c.slug}/ws/projects/{pid}/modules/dashboard/records/").data == []
        assert sup.get(f"/api/super/data/{c.pk}/{pid}/dashboard/").data["records"] == []

    def test_builtin_records_are_listed_but_managed_in_iwms(self, sup):
        c = make_company(sup, "Victor")
        api = admin_client(c)
        pid = api.get(f"/api/t/{c.slug}/ws/me/").data["projects"][0]["id"]
        add_employee(c, pid, "Meena")
        rows = api.get(f"/api/t/{c.slug}/ws/projects/{pid}/modules/staff-creations/records/").data
        assert [r["data"]["employee_name"] for r in rows] == ["Meena"]
        r = api.post(f"/api/t/{c.slug}/ws/projects/{pid}/modules/staff-creations/records/",
                     {"data": {"employee_name": "X"}}, format="json")
        assert r.status_code == 400 and "IWMS screens" in r.data["detail"]

    def test_builtin_fields_cannot_be_changed(self, sup):
        m = Module.objects.get(code="transport-masters")
        r = sup.put(f"/api/super/modules/{m.pk}/", {"code": m.code, "name": m.name, "description": "x",
                                                     "is_active": True, "fields": [{"name": "foo"}]}, format="json")
        assert r.status_code == 400 and "built-in" in str(r.data)
        r = sup.put(f"/api/super/modules/{m.pk}/", {"code": m.code, "name": "Fleet", "description": "x",
                                                     "is_active": True, "fields": m.fields}, format="json")
        assert r.status_code == 200 and r.data["name"] == "Fleet"

# ── company permissions (Console → Companies → Permissions) ───────

def iwms_login(slug, username, password="secret12"):
    """Sign in through the IWMS login (old UI) at the company's address."""
    r = APIClient().post(f"/api/t/{slug}/login/", {"username": username, "password": password}, format="json")
    assert r.status_code == 200, r.content
    return bearer(r.data["access_token"])


def seed_screens(company, *names, module="common-masters"):
    """The IWMS screen catalog (seeded in real installs) for a test company."""
    from app.models.superadmin.screen_management.mainscreen import MainScreen
    from app.models.superadmin.screen_management.userscreen import UserScreen
    from app.models.superadmin.screen_management.userscreenaction import UserScreenAction

    alias = register_tenant_db(company.db_name)
    with tenant_context(alias):
        main = MainScreen.objects.create(mainscreen_name=module, order_no=1)
        for i, name in enumerate(names, start=1):
            UserScreen.objects.create(userscreen_name=name, mainscreen_id=main.pk, order_no=i, icon_name=name,
                                      folder_name=name)
        if not UserScreenAction.objects.exists():
            for action in ("view", "add", "edit", "delete", "use", "import", "export"):
                UserScreenAction.objects.create(action_name=action, variable_name=action)


class TestIwmsScreens:
    def test_admin_gets_exactly_the_granted_permissions(self, sup):
        c = make_company(sup, "Xray", modules=[{"code": "common-masters"}])
        seed_screens(c, "continents", "countries", "states")
        url = f"/api/super/companies/{c.pk}/permissions/"
        data = sup.get(url).data
        project = data["project"]
        common = next(m for m in data["modules"] if m["code"] == "common-masters")
        assert [s["label"] for s in common["screens"]] == ["Continent", "Country", "State"]
        country = next(s for s in common["screens"] if s["name"] == "countries")
        assert not any(a["granted"] for a in country["actions"])          # nothing until granted
        assert {f["label"] for f in country["fields"]} >= {"Continent", "Mobile Code", "Status"}

        r = sup.put(url, {"project": project, "actions": {"countries": ["add"], "states": ["view"]},
                          "fields": {"countries": ["name", "continent_id", "is_active"]}}, format="json")
        assert r.status_code == 200, r.content
        common = next(m for m in r.data["modules"] if m["code"] == "common-masters")
        granted = {s["name"]: [a["name"] for a in s["actions"] if a["granted"]] for s in common["screens"]}
        assert sorted(granted["countries"]) == ["add", "view"]   # add brings view
        assert granted["states"] == ["view"] and granted["continents"] == []

        api = iwms_login(c.slug, "boss")
        payload = api.get(f"/api/t/{c.slug}/login/my-permissions/").data
        perms = payload["permissions"]["common-masters"]
        assert sorted(perms["countries"]) == ["add", "view"] and perms["states"] == ["view"]
        assert "continents" not in perms
        cols = {r["fieldName"]: r["canView"] for r in payload["column_permissions"]["grouped"]["common-masters"]["countries"]}
        assert cols["name"] and cols["continent_id"] and not cols["currency"]

        # View switched off: the screen is gone for the company.
        sup.put(url, {"project": project, "actions": {"states": []}}, format="json")
        perms = iwms_login(c.slug, "boss").get(f"/api/t/{c.slug}/login/my-permissions/").data["permissions"]
        assert "states" not in perms.get("common-masters", {})


class TestIwmsFields:
    def test_module_lists_its_forms_and_fields(self, sup):
        m = Module.objects.get(code="common-masters")
        forms = sup.get(f"/api/super/modules/{m.pk}/forms/").data["forms"]
        assert [f["label"] for f in forms] == ["Continent", "Country", "State"]
        country = forms[1]
        assert {"name", "currency", "mob_code", "continent_id"} <= {x["name"] for x in country["fields"]}
        assert "created_by_id" not in {x["name"] for x in country["fields"]}

    def test_company_fields_reach_the_iwms_screens(self, sup):
        c = make_company(sup, "Zulu", modules=[{"code": "common-masters"}])
        seed_screens(c, "countries")

        url = f"/api/super/companies/{c.pk}/iwms-fields/common-masters/"
        forms = {f["name"]: f for f in sup.get(url).data["forms"]}
        assert forms["countries"]["available"] and not forms["countries"]["customised"]
        assert all(x["enabled"] for x in forms["countries"]["fields"])
        assert forms["continents"]["available"] is False

        r = sup.put(url, {"countries": ["name", "currency"]}, format="json")
        assert r.status_code == 200, r.content
        countries = {f["name"]: f for f in r.data["forms"]}["countries"]
        assert countries["customised"]
        assert {x["name"] for x in countries["fields"] if x["enabled"]} == {"name", "currency"}

        project = sup.get(f"/api/super/companies/{c.pk}/permissions/").data["project"]
        sup.put(f"/api/super/companies/{c.pk}/permissions/", {"project": project, "actions": {"countries": ["view"]}},
                format="json")
        api = iwms_login(c.slug, "boss")
        cols = api.get(f"/api/t/{c.slug}/login/my-permissions/").data["column_permissions"]
        shown = {row["fieldName"]: row["canView"] for row in cols["grouped"]["common-masters"]["countries"]}
        assert shown["name"] and shown["currency"] and not shown["mob_code"]

        # Back to every field: no restriction left.
        sup.put(url, {"countries": None}, format="json")
        cols = iwms_login(c.slug, "boss").get(f"/api/t/{c.slug}/login/my-permissions/").data["column_permissions"]
        assert "countries" not in cols["grouped"].get("common-masters", {})

    def test_unused_module_is_refused(self, sup):
        c = make_company(sup, "Alpha Two", modules=[{"code": "assets"}])
        r = sup.get(f"/api/super/companies/{c.pk}/iwms-fields/common-masters/")
        assert r.status_code == 404


# ── form settings: delete / rename / required / order / extra fields ──

class TestFormConfig:
    def url(self):
        return f"/api/super/modules/{Module.objects.get(code='common-masters').pk}/forms/"

    def test_save_and_read_back(self, sup):
        r = sup.put(self.url(), {"countries": {
            "fields": {"currency": {"label": "Money", "required": True, "order": 1},
                       "mob_code": {"hidden": True}},
            "extras": [{"name": "iso_code", "label": "ISO code", "type": "text", "required": True}],
        }}, format="json")
        assert r.status_code == 200, r.content
        country = {f["name"]: f for f in r.data["forms"]}["countries"]
        fields = {f["name"]: f for f in country["fields"]}
        assert country["fields"][0]["name"] == "currency"           # reordered
        assert fields["currency"]["label"] == "Money" and fields["currency"]["required"]
        assert fields["currency"]["default_label"] == "Currency"
        assert fields["mob_code"]["hidden"]
        assert [e["name"] for e in country["extras"]] == ["iso_code"]

    def test_any_field_can_be_optional_or_deleted(self, sup):
        r = sup.put(self.url(), {"countries": {"fields": {"name": {"required": False},
                                                          "currency": {"hidden": True}}}}, format="json")
        assert r.status_code == 200, r.content
        fields = {f["name"]: f for f in {f["name"]: f for f in r.data["forms"]}["countries"]["fields"]}
        assert fields["name"]["required"] is False and fields["currency"]["hidden"] is True
        r = sup.put(self.url(), {"countries": {"extras": [{"name": "currency", "type": "text"}]}}, format="json")
        assert r.status_code == 400 and "already a field" in str(r.data)

    def test_settings_reach_company_logins(self, sup):
        sup.put(self.url(), {"countries": {
            "fields": {"mob_code": {"hidden": True}, "currency": {"required": True}},
            "extras": [{"name": "iso_code", "label": "ISO code", "type": "text"}],
        }}, format="json")
        c = make_company(sup, "Bravo Two", modules=[{"code": "common-masters"}])
        api = iwms_login(c.slug, "boss")
        cols = api.get(f"/api/t/{c.slug}/login/my-permissions/").data["column_permissions"]
        rows = {r["fieldName"]: r["canView"] for r in cols["grouped"]["common-masters"]["countries"]}
        assert rows["mob_code"] is False and rows["currency"] is True
        cfg = api.get(f"/api/t/{c.slug}/ws/form-config/").data["countries"]
        assert cfg["routes"] == ["common-masters/countries/"] and cfg["extras"][0]["name"] == "iso_code"
        # Required: the API refuses a save that leaves it empty.
        r = api.post(f"/api/t/{c.slug}/common-masters/countries/", {"name": "Freedonia", "currency": ""},
                     format="json")
        assert r.status_code == 400 and "Currency is required." in str(r.json())
        # Extra field values per record.
        r = api.put(f"/api/t/{c.slug}/ws/extras/countries/CNTRY-1/", {"data": {"iso_code": "FD"}}, format="json")
        assert r.status_code == 200
        assert api.get(f"/api/t/{c.slug}/ws/extras/countries/CNTRY-1/").data["data"] == {"iso_code": "FD"}

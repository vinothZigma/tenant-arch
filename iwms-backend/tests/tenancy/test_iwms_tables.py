"""Database level (Edit project): modules → sub-modules → fields decide which
IWMS tables/columns exist in the company DB (app/tenancy/iwms_tables.py)."""
import pytest
from django.apps import apps
from django.db import connections
from rest_framework.test import APIClient

from app.tenancy import iwms_tables as T
from app.tenancy.context import tenant_context
from app.tenancy.db import migrate_tenant_db, register_tenant_db
from app.utils import permission_catalog as catalog

from .test_tenancy_api import env, make_company, seed_screens, sup  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db(transaction=True)

DAILY_TRIP_PLAN = {"app_dailytripassignment", "app_dailytripcollectionpoint", "app_dailytriphouseholdcollection"}


def tables(company):
    return set(connections[register_tenant_db(company.db_name)].introspection.table_names())


def columns(company, table):
    alias = register_tenant_db(company.db_name)
    with connections[alias].cursor() as cursor:
        return {c.name for c in connections[alias].introspection.get_table_description(cursor, table)}


def project_of(sup, company):
    return sup.get(f"/api/super/companies/{company.pk}/projects/").data[0]


def save(sup, company, codes, screens=None):
    p = project_of(sup, company)
    body = {"name": p["name"], "description": "", "admin_usernames": ["boss"], "new_admins": [],
            "modules": [{"code": c} for c in codes]}
    if screens is not None:
        body["screens"] = screens
    r = sup.put(f"/api/super/companies/{company.pk}/projects/{p['id']}/", body, format="json")
    assert r.status_code == 200, r.content
    return {m["code"] for m in r.data[0]["modules"]}


class TestGraph:
    def test_every_sub_module_table_is_a_company_table_owned_once(self):
        screens = {s["name"] for m in catalog.MODULES.values() for s in m["screens"]}
        assert set(T.SCREEN_TABLES) <= screens
        seen = {}
        for screen, names in T.SCREEN_TABLES.items():
            for t in names:
                assert t not in seen, f"{t} in {seen.get(t)} and {screen}"
                seen[t] = screen
        assert set(seen) <= {m._meta.db_table for m in apps.get_app_config("app").get_models()}
        assert not set(T.MODULE_TABLES) & T.CORE_MODULES

    def test_cascade(self):
        on = {"staff-templates", "vehicle-creation", "trip-plans", "daily-trip-assignments",
              "daily-trip-collection-points", "daily-trip-logs", "wards"}
        # Daily Trip Plan off -> what hangs off a daily trip goes too; trip plans stay.
        assert T.cascade_screens(on, on - {"daily-trip-assignments", "daily-trip-collection-points"}) == {
            "staff-templates", "vehicle-creation", "trip-plans", "wards"}
        # Daily trip logs on -> the chain it needs comes too.
        assert T.cascade_screens(set(), {"daily-trip-logs"}) == {
            "daily-trip-logs", "daily-trip-assignments", "trip-plans", "staff-templates", "vehicle-creation"}
        # Module level: Schedule Setup off takes the Daily Operations sub-modules
        # that need trip plans; Staff Notifications doesn't, so the module stays.
        assert T.cascade({"schedule-setup", "schedule-operations", "transport-masters"},
                         {"schedule-operations", "transport-masters"}) == {"schedule-operations", "transport-masters"}
        # Fleet & Reports keeps its report pages; only Weighbridge needs daily trips.
        assert T.cascade({"schedule-setup", "fleet-reports"}, {"fleet-reports"}) == {"fleet-reports"}


class TestTables:
    def test_module_untick_drops_tick_creates(self, sup):
        c = make_company(sup, "Tango", modules=[{"code": "staff-creations"}, {"code": "schedule-operations"},
                                                {"code": "attendance"}])
        have = tables(c)
        assert DAILY_TRIP_PLAN | {"app_tripplan", "app_vehiclecreation", "app_employee"} <= have
        assert "app_complaintticket" not in have and "app_ward" not in have

        # Schedule Setup off: its tables and Daily Operations' tables are dropped.
        mods = save(sup, c, ["staff-creations", "transport-masters", "attendance"])
        assert mods == {"staff-creations", "transport-masters", "attendance"}
        have = tables(c)
        assert not (DAILY_TRIP_PLAN | {"app_tripplan", "app_stafftemplate", "app_staffnotification"}) & have
        assert {"app_vehiclecreation", "app_staffcreationofficedetails", "app_userscreen"} <= have

        # Ticked again: created again, with what it needs.
        mods = save(sup, c, ["staff-creations", "schedule-operations", "attendance"])
        assert {"schedule-operations", "schedule-setup", "transport-masters"} <= mods
        assert DAILY_TRIP_PLAN | {"app_tripplan"} <= tables(c)
        from app.models.core_modules.daily_operations.daily_trip_assignment import DailyTripAssignment
        with tenant_context(register_tenant_db(c.db_name)):
            assert DailyTripAssignment.objects.count() == 0

    def test_sub_module_untick_drops_its_tables(self, sup):
        c = make_company(sup, "Umbra", modules=[{"code": "schedule-operations"}])
        assert DAILY_TRIP_PLAN | {"app_dailytriplog"} <= tables(c)
        # Daily Trip Plan (3 forms) off; trip plans stay.
        save(sup, c, ["schedule-operations", "schedule-setup", "transport-masters"], screens={
            "daily-trip-assignments": {"enabled": False}, "daily-trip-collection-points": {"enabled": False},
            "daily-trip-household-collections": {"enabled": False}})
        have = tables(c)
        assert not DAILY_TRIP_PLAN & have
        assert "app_dailytriplog" not in have                    # needs a daily trip
        assert {"app_tripplan", "app_staffnotification"} <= have  # doesn't
        tree = sup.get(f"/api/super/companies/{c.pk}/structure/?project={project_of(sup, c)['id']}").data
        ops = next(m for m in tree["modules"] if m["code"] == "schedule-operations")
        dtp = next(g for g in ops["sub_modules"] if g["key"] == "daily-trip-plan")
        assert dtp["label"] == "Daily Trip Plan" and len(dtp["forms"]) == 3
        assert not any(f["enabled"] for f in dtp["forms"])
        # On again.
        save(sup, c, ["schedule-operations", "schedule-setup", "transport-masters"], screens={
            "daily-trip-assignments": {"enabled": True}, "daily-trip-collection-points": {"enabled": True},
            "daily-trip-household-collections": {"enabled": True}})
        assert DAILY_TRIP_PLAN <= tables(c)

    def test_tables_stay_while_another_project_uses_them(self, sup):
        c = make_company(sup, "Vega", modules=[{"code": "attendance"}, {"code": "transport-masters"}])
        r = sup.post(f"/api/super/companies/{c.pk}/projects/", {
            "name": "South", "description": "", "admin_usernames": ["boss"], "new_admins": [],
            "modules": [{"code": "transport-masters"}]}, format="json")
        assert r.status_code == 201, r.content
        save(sup, c, ["attendance"])
        assert "app_vehiclecreation" in tables(c)

    def test_migrate_restores_then_drops_again(self, sup):
        c = make_company(sup, "Wolf", modules=[{"code": "attendance"}])
        assert "app_tripplan" not in tables(c)
        migrate_tenant_db(c.db_name)
        assert "app_tripplan" not in tables(c) and "app_employee" in tables(c)

    def test_login_works_with_a_module_dropped(self, sup):
        """Login reads the customer table too: a dropped table reads as empty."""
        c = make_company(sup, "Xenon", modules=[{"code": "staff-creations"}])
        assert "app_customercreation" not in tables(c)
        r = APIClient().post(f"/api/t/{c.slug}/login/", {"username": "boss", "password": "secret12"}, format="json")
        assert r.status_code == 200, r.content


class TestOnlyCompanyAndProjectAreCore:
    def test_staff_roles_audits_can_be_dropped_and_login_still_works(self, sup):
        assert T.CORE_MODULES == {"superadmin-masters"}
        c = make_company(sup, "Kilo", modules=[{"code": "attendance"}])
        have = tables(c)
        # Unticked modules: their tables are gone...
        assert not {"app_department", "app_designation", "app_contractorusertype", "app_projectstaffhierarchy",
                    "app_loginaudit", "permission_audit_logs", "app_auditlog", "app_staffpersonaldetails"} & have
        # ...except what every sign-in needs.
        assert {"app_staffcreationofficedetails", "app_usertype", "app_staffusertype", "app_userscreen",
                "app_companyuserscreenpermission", "app_project"} <= have
        seed_screens(c, "attendance", module="attendance")
        r = APIClient().post(f"/api/t/{c.slug}/login/", {"username": "boss", "password": "secret12"}, format="json")
        assert r.status_code == 200, r.content      # login audit write skipped, not failed
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access_token']}")
        assert api.get(f"/api/t/{c.slug}/login/my-permissions/").status_code == 200

        # Staff Management ticked: its tables come back; unticked again: gone.
        save(sup, c, ["attendance", "staff-creations"])
        assert {"app_department", "app_designation", "app_staffpersonaldetails"} <= tables(c)
        save(sup, c, ["attendance"])
        assert "app_department" not in tables(c) and "app_staffcreationofficedetails" in tables(c)

    def test_staff_access_configuration_can_be_dropped(self, sup):
        c = make_company(sup, "Mike", modules=[{"code": "staff-creations"}, {"code": "attendance"}])
        r = sup.post(f"/api/super/companies/{c.pk}/projects/", {
            "name": "Other", "description": "", "admin_usernames": [], "new_admins": [
                {"username": "other", "password": "secret12", "full_name": "Other", "email": ""}],
            "modules": [{"code": "attendance"}]}, format="json")
        assert r.status_code == 201, r.content
        assert "app_staffaccessconfiguration" in tables(c)
        save(sup, c, ["staff-creations", "attendance"], screens={"staff-access-configuration": {"enabled": False}})
        have = tables(c)
        assert not {"app_staffaccessconfiguration", "app_staffaccessconfigurationpermission"} & have
        assert {"app_staffcreationofficedetails", "staff_access_configuration"} <= have

        r = APIClient().post(f"/api/t/{c.slug}/login/", {"username": "boss", "password": "secret12"}, format="json")
        assert r.status_code == 200, r.content
        # Still only the project boss runs (from the console's project admins).
        assert [p["name"] for p in r.data["projects"]] == ["North"]

        # On again: the tables come back and the managed scope is written again.
        save(sup, c, ["staff-creations", "attendance"], screens={"staff-access-configuration": {"enabled": True}})
        assert "app_staffaccessconfiguration" in tables(c)

    def test_tree_marks_kept_and_shared_forms(self, sup):
        c = make_company(sup, "Lima", modules=[{"code": "attendance"}])
        tree = {m["code"]: m for m in sup.get(f"/api/super/companies/{c.pk}/structure/").data["modules"]}
        assert [m for m, x in tree.items() if x["core"]] == ["superadmin-masters"]
        forms = {f["name"]: f for g in tree["staff-creations"]["sub_modules"] for f in g["forms"]}
        assert forms["department-masters"]["tables"] == ["app_department"]
        assert forms["staffcreation"]["tables"] == ["app_staffpersonaldetails"]
        assert forms["staff-access-configuration"]["tables"] == [
            "app_staffaccessconfiguration", "app_staffaccessconfigurationpermission"]
        assert "app_staffcreationofficedetails" in forms["staffcreation"]["kept_tables"]
        common = {f["name"]: f for g in tree["common-masters"]["sub_modules"] for f in g["forms"]}
        assert common["countries"]["tables"] == ["app_country"] and not common["countries"]["shared"]


class TestSubModulesWithoutTables:
    def test_turned_off_sub_module_is_hidden_from_the_admin(self, sup):
        from .test_tenancy_api import iwms_login

        c = make_company(sup, "Nova", modules=[{"code": "audits"}])
        seed_screens(c, "common-audit", "complaint-audit", "login-audit", module="audits")
        url = f"/api/super/companies/{c.pk}/permissions/"
        project = sup.get(url).data["project"]
        sup.put(url, {"project": project, "actions": {
            "common-audit": ["view"], "complaint-audit": ["view"], "login-audit": ["view"]}}, format="json")
        perms = iwms_login(c.slug, "boss").get(f"/api/t/{c.slug}/login/my-permissions/").data["permissions"]
        assert {"common-audit", "complaint-audit", "login-audit"} <= set(perms["audits"])

        # Common Audit and Complaint Audit off: hidden (and their tables
        # dropped); Login Audit stays.
        save(sup, c, ["audits"], screens={"common-audit": {"enabled": False}, "complaint-audit": {"enabled": False}})
        perms = iwms_login(c.slug, "boss").get(f"/api/t/{c.slug}/login/my-permissions/").data["permissions"]
        assert set(perms["audits"]) == {"login-audit"}
        assert "app_loginaudit" in tables(c) and "common_audit" not in tables(c)
        tree = sup.get(f"/api/super/companies/{c.pk}/structure/?project={project}").data["modules"]
        audits = {f["name"]: f for m in tree if m["code"] == "audits" for g in m["sub_modules"] for f in g["forms"]}
        assert not audits["common-audit"]["enabled"] and audits["common-audit"]["tables"] == ["common_audit"]
        assert not audits["complaint-audit"]["enabled"] and audits["login-audit"]["enabled"]


class TestNoSharedTables:
    def test_each_company_has_its_own_lookups(self, sup):
        from app.models.superadmin.common_masters.country import Country
        from app.tenancy.catalog import is_tenant_model
        from app.tenancy.router import MASTER_DB

        assert is_tenant_model(Country)
        Country._base_manager.using(MASTER_DB).create(name="Template Land")   # the template
        a = make_company(sup, "Oscar", modules=[{"code": "common-masters"}])
        b = make_company(sup, "Papa", modules=[{"code": "common-masters"}])
        ta, tb = register_tenant_db(a.db_name), register_tenant_db(b.db_name)
        assert Country._base_manager.using(ta).filter(name="Template Land").exists()
        Country._base_manager.using(ta).create(name="Only In Oscar")
        assert not Country._base_manager.using(tb).filter(name="Only In Oscar").exists()
        assert not Country._base_manager.using(MASTER_DB).filter(name="Only In Oscar").exists()

        # Common Masters off: Papa's own country table is dropped, Oscar's stays;
        # on again: recreated from the template.
        save(sup, b, ["attendance"])
        assert "app_country" not in tables(b) and "app_country" in tables(a)
        save(sup, b, ["attendance", "common-masters"])
        assert Country._base_manager.using(tb).filter(name="Template Land").exists()


class TestComplaintAuditTable:
    def test_complaint_audit_has_its_own_table(self, sup):
        from app.models.core_modules.complaint_management.ticket import ComplaintTicket
        from app.models.core_modules.complaint_management.transactions import ComplaintStatusHistory
        from app.models.superadmin.audits.complaint_audit import ComplaintAuditLog

        mods = ["complaint-ticket", "complaint-masters", "audits"]
        c = make_company(sup, "Quebec", modules=[{"code": m} for m in mods])
        alias = register_tenant_db(c.db_name)
        assert "complaint_audit_logs" in tables(c)
        with tenant_context(alias):
            t = ComplaintTicket.objects.create(ticket_no="CMP-1", title="Bin not collected")
            ComplaintStatusHistory.objects.create(ticket_id=t.unique_id, to_status_id="ST-OPEN", remarks="logged")
            assert list(ComplaintAuditLog.objects.filter(ticket_id=t.unique_id).values_list("event", flat=True)) \
                == ["created", "status_changed"]

        # Complaint Audit off: its table is dropped, the workflow still works.
        save(sup, c, mods, screens={"complaint-audit": {"enabled": False}})
        assert "complaint_audit_logs" not in tables(c)
        with tenant_context(alias):
            ComplaintStatusHistory.objects.create(ticket_id=t.unique_id, to_status_id="ST-DONE", remarks="fixed")

        # On again: recreated with the whole history, including what happened meanwhile.
        save(sup, c, mods, screens={"complaint-audit": {"enabled": True}})
        with tenant_context(alias):
            assert ComplaintAuditLog.objects.filter(ticket_id=t.unique_id, event="status_changed").count() == 2
        tree = sup.get(f"/api/super/companies/{c.pk}/structure/").data["modules"]
        audits = {f["name"]: f for m in tree if m["code"] == "audits" for g in m["sub_modules"] for f in g["forms"]}
        assert audits["complaint-audit"]["tables"] == ["complaint_audit_logs"]


class TestStaticRouteMap:
    def test_static_route_map_owns_the_route_tables(self, sup):
        route_tables = {"app_routedetourwaypoint", "app_tripplanstaticroute", "app_dailytripstaticroute"}
        mods = ["schedule-operations", "schedule-setup", "transport-masters"]
        c = make_company(sup, "Romeo", modules=[{"code": m} for m in mods])
        assert route_tables <= tables(c)
        save(sup, c, mods, screens={"static-route-map": {"enabled": False}})
        have = tables(c)
        assert not route_tables & have
        assert {"app_tripplan", "app_dailytripassignment"} <= have      # trips keep working
        from app.services import static_route
        with tenant_context(register_tenant_db(c.db_name)):
            assert static_route._routes_off()
            assert static_route.save_plan_static_route(object()) == (None, None, 0)
        save(sup, c, mods, screens={"static-route-map": {"enabled": True}})
        assert route_tables <= tables(c)
        tree = sup.get(f"/api/super/companies/{c.pk}/structure/").data["modules"]
        forms = {f["name"]: f for m in tree for g in m["sub_modules"] for f in g["forms"]}
        assert set(forms["static-route-map"]["tables"]) == route_tables


class TestScreenManagementNotOffered:
    def test_screen_management_is_not_offered_to_companies(self, sup):
        from .test_tenancy_api import iwms_login

        c = make_company(sup, "Sierra", modules=[{"code": "screen-managements"}, {"code": "attendance"}])
        seed_screens(c, "attendance", "userscreens", module="attendance")
        project = project_of(sup, c)
        assert {m["code"] for m in project["modules"]} == {"attendance"}
        tree = sup.get(f"/api/super/companies/{c.pk}/structure/?project={project['id']}").data["modules"]
        assert "screen-managements" not in {m["code"] for m in tree}
        url = f"/api/super/companies/{c.pk}/permissions/"
        data = sup.get(url).data
        assert "screen-managements" not in {m["code"] for m in data["modules"]}
        # Even a leftover grant doesn't reach the admin.
        r = sup.put(url, {"project": data["project"], "actions": {"attendance": ["view"]}}, format="json")
        assert r.status_code == 200, r.content
        perms = iwms_login(c.slug, "boss").get(f"/api/t/{c.slug}/login/my-permissions/").data["permissions"]
        assert "screen-managements" not in perms
        # Its tables stay: every sign-in reads them.
        assert {"app_userscreen", "app_mainscreen", "app_companyuserscreenpermission"} <= tables(c)


class TestColumns:
    def test_unticked_field_drops_its_column(self, sup):
        c = make_company(sup, "Yak", modules=[{"code": "transport-masters"}])
        pid = project_of(sup, c)["id"]
        tree = sup.get(f"/api/super/companies/{c.pk}/structure/?project={pid}").data["modules"]
        form = next(f for m in tree for g in m["sub_modules"] for f in g["forms"] if f["name"] == "vehicle-creation")
        names = {f["name"]: f["label"] for f in form["fields"]}
        assert {"service_record", "capacity", "vehicle_no"} <= set(names)
        assert "unique_id" not in names and "company_id" not in names

        off = {"vehicle-creation": {"enabled": True, "off_fields": ["service_record", "capacity"]}}
        p = sup.post(f"/api/super/companies/{c.pk}/structure-preview/",
                     {"project": pid, "modules": ["transport-masters"], "screens": off}, format="json").data
        assert sorted(p["drop_columns"]) == ["app_vehiclecreation.capacity", "app_vehiclecreation.service_record"]
        assert "service_record" in columns(c, "app_vehiclecreation")       # preview changes nothing

        save(sup, c, ["transport-masters"], screens=off)
        cols = columns(c, "app_vehiclecreation")
        assert "service_record" not in cols and "capacity" not in cols and "vehicle_no" in cols

        from app.models.masters.transport_masters.vehicleCreation import VehicleCreation
        alias = register_tenant_db(c.db_name)
        with tenant_context(alias):
            v = VehicleCreation.objects.create(vehicle_no="TN01AB1234", service_record="x", capacity=3)
            v.vehicle_no = "TN01AB9999"
            v.save()
            got = VehicleCreation.objects.get(pk=v.pk)
            assert got.vehicle_no == "TN01AB9999" and got.service_record is None and got.capacity is None

        save(sup, c, ["transport-masters"], screens={"vehicle-creation": {"enabled": True, "off_fields": []}})
        assert {"service_record", "capacity"} <= columns(c, "app_vehiclecreation")
        with tenant_context(alias):
            assert VehicleCreation.objects.get(pk=v.pk).service_record is None

    def test_permissions_page_never_changes_the_database(self, sup):
        c = make_company(sup, "Zed", modules=[{"code": "transport-masters"}])
        seed_screens(c, "vehicle-creation", module="transport-masters")
        url = f"/api/super/companies/{c.pk}/permissions/"
        project = sup.get(url).data["project"]
        r = sup.put(url, {"project": project, "fields": {"vehicle-creation": ["vehicle_no"]}}, format="json")
        assert r.status_code == 200, r.content
        assert "service_record" in columns(c, "app_vehiclecreation")

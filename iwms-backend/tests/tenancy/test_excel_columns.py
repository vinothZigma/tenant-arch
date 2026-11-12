"""Excel columns: the super admin enables export/import fields per company or
project; the export, the upload and the list pages follow it."""
import io
import re
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from app.models.tenancy.master import ExcelColumnSetting
from app.tenancy.excel_columns import (
    clean_columns, filter_upload_csv, resolve, screen_fields, screen_for_path, screens_tree,
)

from .test_tenancy_api import admin_client, bearer, company_login, env, make_company, sup  # noqa: F401

SCREEN = "fuels"
LIST = "/transport-masters/fuels/"


class TestCatalog:
    def test_every_module_has_its_screens(self):
        modules = {m["name"]: m for m in screens_tree()}
        assert "customers" in modules and "transport-masters" in modules
        fuels = screen_fields(SCREEN)
        assert {"fuel_type", "description"} <= {f["name"] for f in fuels["export"]}
        assert [f["name"] for f in fuels["import"] if f["required"]] == ["fuel_type"]
        # secrets never offered, server-stamped fields never imported
        staff = screen_fields("staffcreation")
        assert "password" not in {f["name"] for f in staff["export"] + staff["import"]}
        assert "unique_id" not in {f["name"] for f in fuels["import"]}
        assert "company_id" not in {f["name"] for f in screen_fields("customercreations")["import"]}

    def test_form_fields_with_form_labels(self):
        countries = screen_fields("countries")
        names = [f["name"] for f in countries["export"]]
        assert "unique_id" not in names and "created_at" not in names and "continent_name" not in names
        continent = next(f for f in countries["export"] if f["name"] == "continent_id")
        assert continent["label"] == "Continent" and continent["source"] == "continent_name"
        # an older setting naming the API field maps to the form field
        assert clean_columns("countries", "export", [{"field": "continent_name"}]) == [
            {"field": "continent_id", "header": "Continent"}]

    def test_screen_for_path(self):
        assert screen_for_path("/api/v1/transport-masters/fuels/") == SCREEN
        assert screen_for_path("/api/v1/transport-masters/fuels/bulk-upload/") == SCREEN
        assert screen_for_path("/api/v1/customer-masters/customercreations/") == "customercreations"
        assert screen_for_path("/api/v1/nothing/here/") is None

    def test_clean_columns(self):
        out = clean_columns(SCREEN, "export", [{"field": "fuel_type", "header": " Fuel "}, {"field": "fuel_type"}])
        assert out == [{"field": "fuel_type", "header": "Fuel"}]
        # import keeps the required fields
        assert clean_columns(SCREEN, "import", [{"field": "description"}]) == [
            {"field": "description", "header": "Description"}, {"field": "fuel_type", "header": "Fuel Type"}]
        for bad in ([{"field": "nope"}], "x", [], [{"field": "fuel_type", "header": "A"},
                                                   {"field": "description", "header": "a"}]):
            with pytest.raises(ValidationError):
                clean_columns(SCREEN, "export", bad)
        with pytest.raises(ValidationError):
            clean_columns("nope", "export", [{"field": "x"}])

    def test_filter_upload_csv(self):
        raw = "fuel_type,description,company_id,extra\nDiesel,d,C1,x\n"
        text, removed = filter_upload_csv(raw, {"fuel_type"}, {"fuel_type", "description", "company_id"})
        assert removed == ["description"]
        assert text.splitlines() == ["fuel_type,company_id,extra", "Diesel,C1,x"]
        # lookup fields filled by name count as the field
        text, removed = filter_upload_csv("ward_name,zone_name,customer_name\nW1,Z1,R\n",
                                          {"ward_id", "customer_name"}, {"ward_id", "zone_id", "customer_name"})
        assert removed == ["zone_name"]
        assert text.splitlines()[0] == "ward_name,customer_name"


@pytest.mark.django_db(transaction=True)
class TestSettings:
    def test_project_then_company_then_every_field(self, sup):  # noqa: F811
        c = make_company(sup, "Acme")
        url = f"/api/super/companies/{c.pk}/excel-columns/"
        state = sup.get(url).data
        pid = state["projects"][0]["id"]
        assert state["settings"] == {}
        assert resolve(c, pid, SCREEN, "export") == (None, None)

        r = sup.put(url, {"project_id": "", "screen": SCREEN, "kind": "export",
                          "columns": [{"field": "fuel_type", "header": "Fuel"}]}, format="json")
        assert r.status_code == 200, r.content
        assert r.data["settings"][SCREEN]["export"]["source"] == "company"
        r = sup.put(url, {"project_id": pid, "screen": SCREEN, "kind": "export",
                          "columns": [{"field": "description"}, {"field": "fuel_type"}]}, format="json")
        assert r.data["settings"][SCREEN]["export"]["source"] == "project"
        assert resolve(c, pid, SCREEN, "export")[1] == "project"
        assert resolve(c, "", SCREEN, "export") == ([{"field": "fuel_type", "header": "Fuel"}], "company")

        # what the company's list page receives
        api = admin_client(c)
        r = api.get(f"/api/t/{c.slug}/ws/excel-columns/", {"path": LIST, "project_id": pid})
        assert r.status_code == 200, r.content
        assert r.data["screen"] == SCREEN
        assert [col["field"] for col in r.data["export"]] == ["description", "fuel_type"]
        assert r.data["import"] is None

        # removing the project's row falls back to the company's
        sup.put(url, {"project_id": pid, "screen": SCREEN, "kind": "export", "columns": None}, format="json")
        assert resolve(c, pid, SCREEN, "export")[1] == "company"

    def test_rejects_bad_input(self, sup):  # noqa: F811
        c = make_company(sup, "Acme")
        url = f"/api/super/companies/{c.pk}/excel-columns/"
        body = {"project_id": "", "screen": SCREEN, "kind": "export", "columns": [{"field": "fuel_type"}]}
        assert sup.put(url, {**body, "project_id": "nope"}, format="json").status_code == 404
        assert sup.put(url, {**body, "kind": "both"}, format="json").status_code == 400
        assert sup.put(url, {**body, "screen": "nope"}, format="json").status_code == 400
        assert sup.put(url, {**body, "columns": [{"field": "password"}]}, format="json").status_code == 400
        assert not ExcelColumnSetting.objects.exists()

    def test_requires_super_admin(self, env):  # noqa: F811
        assert APIClient().get("/api/super/excel-screens/").status_code == 401

    def test_screens_endpoint(self, sup):  # noqa: F811
        r = sup.get("/api/super/excel-screens/")
        assert r.status_code == 200
        assert any(s["name"] == SCREEN for m in r.data["modules"] for s in m["screens"])


@pytest.mark.django_db(transaction=True)
class TestEnforcement:
    def _setup(self, sup, kind, columns):  # noqa: F811
        c = make_company(sup, "Acme")
        sup.put(f"/api/super/companies/{c.pk}/excel-columns/",
                {"project_id": "", "screen": SCREEN, "kind": kind, "columns": columns}, format="json")
        # the developer login holds every IWMS grant (incl. export/import)
        r = company_login(c.slug, "developer", "devpass123")
        assert r.status_code == 200, r.content
        return c, bearer(r.data["token"])

    def test_export_writes_only_enabled_columns(self, sup):  # noqa: F811
        c, api = self._setup(sup, "export", [{"field": "fuel_type", "header": "Fuel"}])
        r = api.post(f"/api/t/{c.slug}/exports/excel/", {"path": LIST, "params": {},
                     "columns": [{"header": "Desc", "field": "description"}]}, format="json")
        assert r.status_code == 200, r.content
        strings = zipfile.ZipFile(io.BytesIO(r.content)).read("xl/sharedStrings.xml").decode()
        assert re.findall(r"<t[^>]*>([^<]*)</t>", strings)[0] == "Fuel"
        assert "Desc" not in strings

    def test_upload_drops_disabled_columns(self, sup):  # noqa: F811
        c, api = self._setup(sup, "import", [{"field": "fuel_type"}])
        csv = b"fuel_type,description\nBio Diesel,should be dropped\n"
        r = api.post(f"/api/t/{c.slug}/transport-masters/fuels/bulk-upload/",
                     {"file": SimpleUploadedFile("up.csv", csv, content_type="text/csv")}, format="multipart")
        assert r.status_code == 200, r.content
        assert r.data["success_count"] == 1, r.data
        r = api.get(f"/api/t/{c.slug}/transport-masters/fuels/", {"search": "Bio Diesel"})
        rows = r.data["results"] if isinstance(r.data, dict) else r.data
        row = next(x for x in rows if x["fuel_type"] == "Bio Diesel")
        assert not row["description"]

    def test_upload_without_setting_keeps_every_column(self, sup):  # noqa: F811
        c, api = self._setup(sup, "export", [{"field": "fuel_type"}])   # export only
        csv = b"fuel_type,description\nBio Gas,kept\n"
        r = api.post(f"/api/t/{c.slug}/transport-masters/fuels/bulk-upload/",
                     {"file": SimpleUploadedFile("up.csv", csv, content_type="text/csv")}, format="multipart")
        assert r.data["success_count"] == 1, r.data
        rows = api.get(f"/api/t/{c.slug}/transport-masters/fuels/", {"search": "Bio Gas"}).data
        rows = rows["results"] if isinstance(rows, dict) else rows
        assert next(x for x in rows if x["fuel_type"] == "Bio Gas")["description"] == "kept"


@pytest.mark.django_db(transaction=True)
class TestUnavailableFields:
    """Fields hidden in Permissions & fields can't be chosen or exported."""

    def _hide_description(self, sup):  # noqa: F811
        from app.tenancy.permissions import save_company_permissions

        from .test_tenancy_api import seed_screens

        c = make_company(sup, "Acme")
        seed_screens(c, SCREEN, module="transport-masters")
        pid = sup.get(f"/api/super/companies/{c.pk}/excel-columns/").data["projects"][0]["id"]
        save_company_permissions(c, {
            "project": pid,
            "actions": {SCREEN: ["view", "add", "import", "export"]},
            "fields": {SCREEN: ["fuel_type"]},          # description hidden
        })
        return c, pid

    def test_console_marks_and_rejects_hidden_field(self, sup):  # noqa: F811
        from app.tenancy.excel_columns import PERMISSION_REASON

        c, pid = self._hide_description(sup)
        url = f"/api/super/companies/{c.pk}/excel-columns/"
        state = sup.get(url, {"project_id": pid}).data
        assert state["availability"][SCREEN]["export"]["description"] == PERMISSION_REASON
        assert "fuel_type" not in state["availability"][SCREEN]["export"]
        # the only project hides it -> hidden for the whole company too
        company = sup.get(url).data["availability"][SCREEN]["export"]
        assert company["description"] == "Hidden in Permissions & fields for North"
        # only the form's fields are listed (no created_at / unique_id)
        assert "created_at" not in {f["name"] for f in screen_fields(SCREEN)["export"]}

        r = sup.put(url, {"project_id": pid, "screen": SCREEN, "kind": "export",
                          "columns": [{"field": "description"}]}, format="json")
        assert r.status_code == 400
        assert "Hidden in Permissions" in str(r.data)
        r = sup.put(url, {"project_id": pid, "screen": SCREEN, "kind": "export",
                          "columns": [{"field": "fuel_type"}]}, format="json")
        assert r.status_code == 200

    def test_list_page_and_export_leave_it_out(self, sup):  # noqa: F811
        from .test_tenancy_api import iwms_login

        c, pid = self._hide_description(sup)
        api = iwms_login(c.slug, "boss")
        r = api.get(f"/api/t/{c.slug}/ws/excel-columns/", {"path": LIST, "project_id": pid})
        assert "description" not in [col["field"] for col in r.data["export"]]
        assert [col["field"] for col in r.data["import"]] == ["fuel_type"]

        r = api.post(f"/api/t/{c.slug}/exports/excel/", {"path": LIST, "params": {"project_id": pid}},
                     format="json")
        assert r.status_code == 200, r.content
        strings = zipfile.ZipFile(io.BytesIO(r.content)).read("xl/sharedStrings.xml").decode()
        assert "Fuel Type" in strings and "Description" not in strings

        # the developer login sees every field
        dev = bearer(company_login(c.slug, "developer", "devpass123").data["token"])
        r = dev.get(f"/api/t/{c.slug}/ws/excel-columns/", {"path": LIST, "project_id": pid})
        assert r.data["export"] is None

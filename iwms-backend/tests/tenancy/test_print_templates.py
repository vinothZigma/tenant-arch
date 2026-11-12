"""Print templates: super admin designs + assigns; companies read the one that applies."""
import pytest
from rest_framework.test import APIClient

from app.models.tenancy.master import PrintTemplate
from app.tenancy.print_templates import DEFAULT_LAYOUT, clean_layout, ensure_sample_templates

from .test_tenancy_api import admin_client, env, make_company, sup  # noqa: F401  (fixtures)

pytestmark = pytest.mark.django_db(transaction=True)
REPORT = "daily-trip-log"


@pytest.fixture
def samples(env):  # noqa: F811
    ensure_sample_templates()
    return {t.name: t for t in PrintTemplate.objects.filter(report_code=REPORT)}


class TestCleanLayout:
    def test_partial_layout_gets_defaults_and_signature_count(self):
        out = clean_layout(REPORT, {"logo": "right", "signature_layout": "left-right", "signatures": ["A"]})
        assert out["logo"] == "right"
        assert out["signatures"] == ["A", "Signature"]
        assert out["paper"] == DEFAULT_LAYOUT["paper"]

    def test_rejects_bad_values(self):
        from rest_framework.exceptions import ValidationError

        for bad in ({"logo": "top"}, {"accent_color": "red"}, {"columns": [{"key": "nope"}]},
                    {"font_size": 40}, {"show_date": "yes"}):
            with pytest.raises(ValidationError):
                clean_layout(REPORT, bad)

    def test_unknown_keys_dropped(self):
        assert "script" not in clean_layout(REPORT, {"script": "<x>"})


class TestSuperAdmin:
    def test_list_has_reports_and_samples(self, sup, samples):  # noqa: F811
        r = sup.get("/api/super/print-templates/")
        assert r.status_code == 200
        assert r.data["reports"][0]["code"] == REPORT
        assert len(r.data["templates"]) == 4
        assert sum(t["is_default"] for t in r.data["templates"]) == 1

    def test_create_make_default_and_delete_rules(self, sup, samples):  # noqa: F811
        r = sup.post("/api/super/print-templates/", {
            "report_code": REPORT, "name": "Mine", "layout": {"columns": [{"key": "vehicle", "label": "Vehicle No"}]},
            "is_default": True,
        }, format="json")
        assert r.status_code == 201, r.content
        assert r.data["layout"]["columns"] == [{"key": "vehicle", "label": "Vehicle No"}]
        assert PrintTemplate.objects.filter(report_code=REPORT, is_default=True).get().name == "Mine"
        # the default can't be deleted; the old default can
        assert sup.delete(f"/api/super/print-templates/{r.data['id']}/").status_code == 400
        old = samples["Classic – sign right"]
        assert sup.delete(f"/api/super/print-templates/{old.pk}/").status_code == 204

    def test_requires_super_admin(self, env):  # noqa: F811
        assert APIClient().get("/api/super/print-templates/").status_code == 401


class TestAssignment:
    def test_project_then_company_then_default(self, sup, samples):  # noqa: F811
        c = make_company(sup, "Acme")
        url = f"/api/super/companies/{c.pk}/print-templates/"
        state = sup.get(url).data
        pid = state["projects"][0]["id"]
        default = samples["Classic – sign right"]
        assert state["effective"][f"{REPORT}|{pid}"] == {"template": default.pk, "source": "default"}

        company_t, project_t = samples["Minimal"], samples["Two signatures"]
        sup.put(url, {"report_code": REPORT, "project_id": "", "template": company_t.pk}, format="json")
        state = sup.put(url, {"report_code": REPORT, "project_id": pid, "template": project_t.pk}, format="json").data
        assert state["effective"][f"{REPORT}|{pid}"] == {"template": project_t.pk, "source": "project"}
        assert state["effective"][f"{REPORT}|"] == {"template": company_t.pk, "source": "company"}

        # what the company's IWMS screen receives
        api = admin_client(c)
        r = api.get(f"/api/t/{c.slug}/ws/print-template/{REPORT}/?project_id={pid}")
        assert r.status_code == 200, r.content
        assert r.data["template"]["name"] == "Two signatures"
        assert r.data["company"]["name"] == "Acme"
        assert r.data["project"]["name"] == "North"
        r = api.get(f"/api/t/{c.slug}/ws/print-template/{REPORT}/")
        assert r.data["template"]["name"] == "Minimal"

        # removing the project's row falls back to the company's
        state = sup.put(url, {"report_code": REPORT, "project_id": pid, "template": None}, format="json").data
        assert state["effective"][f"{REPORT}|{pid}"]["source"] == "company"

    def test_rejects_unknown_project_and_wrong_report(self, sup, samples):  # noqa: F811
        c = make_company(sup, "Acme")
        url = f"/api/super/companies/{c.pk}/print-templates/"
        t = samples["Minimal"]
        assert sup.put(url, {"report_code": REPORT, "project_id": "nope", "template": t.pk},
                       format="json").status_code == 404
        assert sup.put(url, {"report_code": "other", "project_id": "", "template": t.pk},
                       format="json").status_code == 400

    def test_unknown_report_404_for_company(self, sup, samples):  # noqa: F811
        c = make_company(sup, "Acme")
        assert admin_client(c).get(f"/api/t/{c.slug}/ws/print-template/nope/").status_code == 404

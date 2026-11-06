"""Phase 1 of database-per-company tenancy (app/tenancy/).

Runs on the SQLite fallback: the master DB is the in-memory test DB and each
company DB is a file in a temporary TENANT_SQLITE_DIR.
"""
import pytest
from django.apps import apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections

from app.models.superadmin.common_masters.continent import Continent
from app.models.superadmin_masters.company import Company
from app.models.superadmin_masters.project import Project
from app.tenancy import db as tenant_db
from app.tenancy.catalog import is_tenant_model
from app.tenancy.context import TenantNotSelected, get_current_tenant_db, tenant_context
from app.tenancy.router import TenantRouter
from app.tenancy.services import deprovision_company, provision_company
from app.tenancy.slugs import db_name_for_slug, make_slug, validate_db_name

TENANT_TABLES = {m._meta.db_table for m in apps.get_models() if is_tenant_model(m)}
MASTER_TABLES = {m._meta.db_table for m in apps.get_models() if not is_tenant_model(m)}


# tenant_dirs fixture: tests/tenancy/conftest.py


# ── slugs and DB names ──────────────────────────────────────────────

@pytest.mark.django_db
class TestSlugs:
    def test_slug_and_db_name_from_name(self):
        assert make_slug("Tata Motors") == "tata-motors"
        assert db_name_for_slug("tata-motors") == "iwms_company_tata_motors"

    def test_duplicates_get_a_number(self):
        Company.objects.create(name="Tata Motors")
        Company.objects.create(name="Tata  Motors!")
        assert set(Company.objects.values_list("slug", flat=True)) == {"tata-motors", "tata-motors-2"}
        assert make_slug("Tata Motors") == "tata-motors-3"

    @pytest.mark.parametrize("name", ["Admin", "superadmin", "API", "Dashboard", "auth"])
    def test_reserved_slugs_are_never_used(self, name):
        slug = make_slug(name)
        assert slug.endswith("-2")

    def test_empty_name_still_gets_a_slug(self):
        assert make_slug("!!!") == "company"

    def test_long_names_fit_mysql_identifier_limit(self):
        company = Company.objects.create(name="X" * 150)
        assert len(company.slug) <= 40
        assert len(company.db_name) <= 64

    @pytest.mark.parametrize("bad", [
        "", "Company_A", "a-b", "a;drop database x", "a`b", "x" * 65, None,
    ])
    def test_unsafe_db_names_are_rejected(self, bad):
        with pytest.raises(ValueError):
            validate_db_name(bad)

    def test_company_save_fills_slug_once(self):
        company = Company.objects.create(name="Blue Planet")
        assert (company.slug, company.db_name) == ("blue-planet", "iwms_company_blue_planet")
        company.name = "Renamed"
        company.save(update_fields=["name"])
        company.refresh_from_db()
        assert company.slug == "blue-planet"  # URL and DB never move


# ── router ──────────────────────────────────────────────────────────

class TestRouterMigrations:
    router = TenantRouter()

    def test_master_models_only_in_master(self, settings):
        for mode in ("shared", "per_db"):
            settings.TENANCY_MODE = mode
            assert self.router.allow_migrate("default", "app", "company")
            assert not self.router.allow_migrate("iwms_company_x", "app", "company")
            assert not self.router.allow_migrate("iwms_company_x", "auth", "group")
            assert not self.router.allow_migrate("iwms_company_x", "app", "user")

    def test_company_models_follow_mode(self, settings):
        settings.TENANCY_MODE = "shared"
        assert self.router.allow_migrate("default", "app", "vehiclecreation")
        assert self.router.allow_migrate("iwms_company_x", "app", "vehiclecreation")
        settings.TENANCY_MODE = "per_db"
        assert not self.router.allow_migrate("default", "app", "vehiclecreation")
        assert self.router.allow_migrate("iwms_company_x", "app", "vehiclecreation")

    def test_unhinted_data_migrations_run_on_master_only(self):
        assert self.router.allow_migrate("default", "app")
        assert not self.router.allow_migrate("iwms_company_x", "app")


class TestRouterQueries:
    router = TenantRouter()

    def test_shared_mode_changes_nothing(self, settings):
        settings.TENANCY_MODE = "shared"
        assert self.router.db_for_read(Project) is None
        with tenant_context("iwms_company_x"):
            assert self.router.db_for_write(Project) is None

    def test_per_db_company_query_without_company_fails_loudly(self, settings):
        settings.TENANCY_MODE = "per_db"
        assert get_current_tenant_db() is None
        with pytest.raises(TenantNotSelected):
            self.router.db_for_read(Project)
        with pytest.raises(TenantNotSelected):
            self.router.db_for_write(Project)

    def test_per_db_routes_to_selected_company(self, settings):
        settings.TENANCY_MODE = "per_db"
        with tenant_context("iwms_company_a"):
            assert self.router.db_for_read(Project) == "iwms_company_a"
            with tenant_context("iwms_company_b"):
                assert self.router.db_for_write(Project) == "iwms_company_b"
            assert self.router.db_for_read(Project) == "iwms_company_a"
            assert self.router.db_for_read(Company) == "default"
            # No IWMS table is shared: each company has its own continents.
            assert self.router.db_for_read(Continent) == "iwms_company_a"
        assert get_current_tenant_db() is None


# ── creating and dropping company databases ─────────────────────────

@pytest.mark.django_db(transaction=True)
class TestProvisioning:
    def test_creating_a_company_creates_its_db_with_company_tables_only(self, tenant_dirs):
        company = provision_company(name="Tata Motors")

        assert company.db_name == "iwms_company_tata_motors"
        assert (tenant_dirs / "tenant_dbs" / "iwms_company_tata_motors.sqlite3").exists()
        tables = tenant_db.tenant_table_names(company.db_name)
        assert TENANT_TABLES <= tables
        assert not (tables & MASTER_TABLES)
        assert "auth_user" not in tables and "django_session" not in tables

    def test_company_rows_land_only_in_the_selected_company_db(self, tenant_dirs, settings):
        a = provision_company(name="Company A")
        b = provision_company(name="Company B")
        settings.TENANCY_MODE = "per_db"

        with tenant_context(a.db_name):
            Project.objects.create(name="Palakkad", company_id=a.unique_id)
        with tenant_context(b.db_name):
            assert Project.objects.count() == 0
        with tenant_context(a.db_name):
            assert list(Project.objects.values_list("name", flat=True)) == ["Palakkad"]
        assert Company.objects.count() == 2  # master query, no company needed
        with pytest.raises(TenantNotSelected):
            Project.objects.count()

    def test_failed_creation_leaves_nothing_behind(self, tenant_dirs, monkeypatch):
        def broken_migrate(db_name, verbosity=0):
            tenant_db.register_tenant_db(db_name)
            tenant_db.sqlite_path(db_name).touch()
            raise RuntimeError("migration exploded")

        monkeypatch.setattr("app.tenancy.db.migrate_tenant_db", broken_migrate)
        with pytest.raises(RuntimeError):
            provision_company(name="Doomed Co")

        assert not Company.objects.filter(name="Doomed Co").exists()
        assert not (tenant_dirs / "tenant_dbs" / "iwms_company_doomed_co.sqlite3").exists()
        assert "iwms_company_doomed_co" not in connections.databases

    def test_drop_backs_up_first(self, tenant_dirs):
        company = provision_company(name="Short Lived")
        backup = deprovision_company(company, delete_row=True)

        assert backup is not None and backup.exists()
        assert backup.parent == tenant_dirs / "backups"
        assert "drop-company-db" in backup.name
        assert not tenant_db.tenant_db_exists(company.db_name)
        assert not Company.objects.filter(pk=company.pk).exists()

    def test_migrate_tenants_reports_broken_company_and_continues(self, tenant_dirs, monkeypatch):
        good = provision_company(name="Good Co")
        bad = provision_company(name="Bad Co")
        real = tenant_db.migrate_tenant_db

        def flaky(db_name, verbosity=0):
            if db_name == bad.db_name:
                raise RuntimeError("disk on fire")
            return real(db_name, verbosity=verbosity)

        monkeypatch.setattr("app.management.commands.migrate_tenants.migrate_tenant_db", flaky)
        with pytest.raises(CommandError, match=r"1 company database\(s\) migrated, 1 failed"):
            call_command("migrate_tenants")
        assert tenant_db.tenant_db_exists(good.db_name)

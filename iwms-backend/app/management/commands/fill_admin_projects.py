from django.core.management.base import BaseCommand

from app.models.superadmin.staff_management.staffcreation import Staffcreation
from app.models.superadmin_masters.company import Company
from app.tenancy.db import register_tenant_db
from app.tenancy.projects import fill_admin_projects


class Command(BaseCommand):
    help = ("Point every company admin's staff row (project_id) at a project they run, so they show "
            "in the IWMS Staff list of that project. Safe to run again.")

    def handle(self, *args, **options):
        for company in Company.objects.filter(is_deleted=False).exclude(db_name=None):
            try:
                alias = register_tenant_db(company.db_name)
                before = dict(Staffcreation.objects.using(alias).filter(
                    is_company_admin=True, is_deleted=False).values_list("pk", "project_id"))
                fill_admin_projects(alias)
                after = dict(Staffcreation.objects.using(alias).filter(
                    pk__in=list(before)).values_list("pk", "project_id"))
                changed = sum(1 for pk in before if before[pk] != after.get(pk))
                result = f"{changed} admin(s) updated"
            except Exception as exc:  # one broken company must not stop the rest
                result = f"FAILED: {exc}"
            self.stdout.write(f"{company.name}: {result}")

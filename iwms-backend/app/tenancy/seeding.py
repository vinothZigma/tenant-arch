"""`manage.py seed` with TENANCY_MODE=per_db: the IWMS seeders write into one
company's own database (default Blue Planet), then the company is made usable
in the console: every module on for each of its projects (so no table is
dropped or read as empty), CompanyModule/staff access synced, a company
admin (ADMIN_USERNAME) running every project plus the developer login and a plan assigned when it has none (an existing plan is kept;
limits are not checked while seeding)."""
from django.db.models import Q

from app.models.superadmin_masters.company import Company
from app.models.superadmin_masters.project import Project
from app.models.tenancy.master import Module, Plan
from app.models.tenancy.tenant import ProjectModule

from . import iwms_tables
from .context import tenant_context
from .db import create_tenant_db, register_tenant_db, tenant_db_exists


def _company(key):
    company = Company.objects.filter(Q(name__iexact=key) | Q(slug__iexact=key), is_deleted=False).first()
    if company is None:
        # First run on an empty master DB: the company seeder makes it, give it its DB.
        company = Company.objects.create(name=key, is_active=True)
    if not company.db_name:
        company.save()  # fills slug/db_name
    return company


def enable_all_modules(alias):
    """Every active module on (all actions) for every live project."""
    codes = list(Module.objects.filter(is_active=True).values_list("code", flat=True))
    caps = {"can_create": True, "can_edit": True, "can_delete": True}
    added = 0
    for project in Project.objects.using(alias).filter(is_deleted=False):
        for code in codes:
            _, new = ProjectModule.all_objects.using(alias).update_or_create(
                project=project, module_code=code,
                defaults={**caps, "is_deleted": False, "deleted_at": None, "is_active": True})
            added += new
    return added


# The company admin the seed gives the company (demo login, like the seeders'
# own staff passwords). Runs every project of the company.
ADMIN_USERNAME, ADMIN_PASSWORD = "bp_admin", "Admin@123"


def ensure_admins(company, alias):
    """Company admin running every live project + the hidden developer login."""
    from app.models.superadmin.staff_management.staffcreation import Staffcreation

    from .people import create_login, ensure_developer
    from .projects import set_admin_projects

    lines = [f"Developer: {ensure_developer(company)}"]
    project_ids = list(Project.objects.using(alias).filter(is_deleted=False).values_list("pk", flat=True))
    admin = Staffcreation.objects.using(alias).filter(username__iexact=ADMIN_USERNAME).first()
    if admin is None:
        admin = create_login(alias, company, username=ADMIN_USERNAME, password=ADMIN_PASSWORD,
                             full_name=f"{company.name} Admin",
                             project_id=project_ids[0] if project_ids else None, is_company_admin=True)
        lines.append(f"Company admin {ADMIN_USERNAME} / {ADMIN_PASSWORD}: created")
    elif not admin.is_company_admin:
        lines.append(f"Company admin: skipped, a normal user is already called {ADMIN_USERNAME}")
        return lines
    else:
        lines.append(f"Company admin {ADMIN_USERNAME}: exists (password unchanged)")
    if project_ids:
        set_admin_projects(company, admin, project_ids)
    return lines


def seed_company(cmd, seeders, key, plan_code):
    out, style = cmd.stdout, cmd.style
    company = _company(key)
    if not tenant_db_exists(company.db_name):
        create_tenant_db(company.db_name)
    alias = register_tenant_db(company.db_name)
    # Tables of modules no project used were dropped: bring them all back first.
    iwms_tables.restore_all(alias)
    out.write(style.WARNING(f"Seeding {company.name} into {company.db_name}...\n"))

    # Plan limits are for the company's own users, not for seeding: the plan
    # is set aside while the seeders run and given back afterwards.
    kept_plan_id = company.plan_id
    Company.objects.filter(pk=company.pk).update(plan=None)
    try:
        with tenant_context(alias):
            for seeder_cls in seeders:
                out.write(style.NOTICE(f"Running {seeder_cls.__name__}"))
                seeder_cls().run()

        company = Company.objects.get(pk=company.pk)
        out.write(f"Modules switched on for projects: {enable_all_modules(alias)} added")

        from .projects import fill_admin_projects, sync_company_modules

        sync_company_modules(company)
        for line in ensure_admins(company, alias):
            out.write(line)
        fill_admin_projects(alias)
    finally:
        Company.objects.filter(pk=company.pk).update(plan_id=kept_plan_id)

    if kept_plan_id is None:
        plan = Plan.objects.filter(code=plan_code).first()
        if plan is None:
            out.write(style.WARNING(f"Plan '{plan_code}' not found (run seed_defaults) - no plan given."))
        else:
            Company.objects.filter(pk=company.pk).update(plan=plan)
            out.write(f"Plan: {plan.name}")
    else:
        out.write(f"Plan kept: {Plan.objects.get(pk=kept_plan_id).name}")
    out.write(style.SUCCESS(f"\nSeeding {company.name} completed successfully."))

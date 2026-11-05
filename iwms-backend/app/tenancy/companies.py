"""Creating and deleting companies (the super admin's wizard)."""
from rest_framework.exceptions import ValidationError

from app.models.superadmin_masters.company import Company
from app.models.tenancy.master import CompanyModule, Module

from .db import create_tenant_db, drop_tenant_db, register_tenant_db, tenant_db_exists
from .people import clean_new_admins, create_admins, ensure_developer
from .projects import admins_qs, clean_project, save_project, sync_company_modules
from .shared_rows import copy_shared_rows
from .staff_access import refresh_staff_access


def create_company(*, name, plan, admins, projects, contact_email="", theme_color="#4f46e5",
                   module_fields=None, description=None):
    """Company row + database + admins + projects + developer, in one go.

    admins:   [{username, password, full_name, email}]          (at least one)
    projects: [{name, description, modules: [{code, can_create, can_edit, can_delete}],
                admin_usernames: [...]}]                          (each needs an admin)
    module_fields: {module_code: [field defs]} — company's own fields.
    On any failure the new DB is dropped and the row deleted.
    """
    name = (name or "").strip()
    if not name:
        raise ValidationError({"name": "Give the company a name."})
    if Company.objects.filter(name__iexact=name, is_deleted=False).exists():
        raise ValidationError({"name": f'A company called "{name}" already exists.'})
    if not admins:
        raise ValidationError({"admins": "Add at least one company admin."})
    if not projects:
        raise ValidationError({"projects": "Add at least one project."})
    if len(admins) > plan.user_limit:
        raise ValidationError({"admins": (
            f"The {plan.name} plan allows {plan.user_limit} users; you added {len(admins)} admins.")})
    usernames = [(a.get("username") or "").strip().lower() for a in admins if isinstance(a, dict)]
    names = [(p.get("name") or "").strip().lower() for p in projects if isinstance(p, dict)]
    if len(set(names)) != len(names):
        raise ValidationError({"projects": "Each project needs a different name."})
    for p in projects:
        assigned = [u.lower() for u in (p.get("admin_usernames") or [])]
        if not assigned:
            raise ValidationError({"projects": f'{p.get("name") or "A project"}: choose at least one admin.'})
        unknown = set(assigned) - set(usernames)
        if unknown:
            raise ValidationError({"projects": f'{p.get("name")}: unknown admin {", ".join(sorted(unknown))}.'})

    company = Company.objects.create(
        name=name, description=description, plan=plan, contact_email=contact_email or "",
        theme_color=theme_color or "#4f46e5",
    )
    try:
        alias = create_tenant_db(company.db_name)
        # Screen catalog, user types, ... so the IWMS screens work in it too.
        copy_shared_rows(alias)
        cleaned = clean_new_admins(admins, alias)
        create_admins(cleaned, alias, company)
        # Custom fields first, so each module table is created with the right columns.
        used = {m.get("code") for p in projects for m in (p.get("modules") or []) if isinstance(m, dict)}
        for module in Module.objects.filter(code__in=used):
            CompanyModule.objects.create(
                company=company, module=module, fields=(module_fields or {}).get(module.code))
        for p in projects:
            clean_project(p, alias)
            save_project(company, p)
        sync_company_modules(company)
        ensure_developer(company)
        refresh_staff_access(alias)  # the developer's IWMS scope too
    except Exception:
        drop_tenant_db(company.db_name)
        Company.objects.filter(pk=company.pk).delete()
        raise
    return company


def delete_company(company, backup=True):
    """Drop the company DB (backed up first) and its logo, delete the row."""
    from .backup import backup_database

    if company.db_name and tenant_db_exists(company.db_name):
        if backup:
            backup_database(company.db_name, reason="delete-company")
        drop_tenant_db(company.db_name)
    if company.company_logo:
        company.company_logo.delete(save=False)
    Company.objects.filter(pk=company.pk).delete()


def admin_usernames(company):
    alias = register_tenant_db(company.db_name)
    return list(admins_qs(alias).values_list("username", flat=True))

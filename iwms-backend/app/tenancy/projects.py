"""Projects with their modules (caps) and admins — super admin tools.

Projects are IWMS Project rows in the company DB. The super admin decides
each project's modules, what may be done in each (create/edit/delete; view
is always on) and which company admin(s) run it.
"""
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from app.models.superadmin.staff_management.staffcreation import Staffcreation
from app.models.superadmin_masters.project import Project
from app.models.tenancy.master import CompanyModule, Module
from app.models.tenancy.tenant import ModulePermission, ProjectAdmin, ProjectModule

from . import iwms_tables, module_tables
from .billing import user_room
from .context import tenant_context
from .db import register_tenant_db
from .modules import project_counts
from .people import clean_new_admins, create_admins, login_json
from .staff_access import refresh_staff_access

CAPS = ("can_create", "can_edit", "can_delete")


def admins_qs(alias):
    """Real company admins (developer excluded)."""
    return Staffcreation.objects.using(alias).filter(is_company_admin=True, is_developer=False, is_deleted=False)


def live_projects(alias):
    return Project.objects.using(alias).filter(is_deleted=False)


def clean_project(data, alias, project=None):
    """Validate {name, description, modules: [{code, can_create, can_edit, can_delete}]}."""
    name = (data.get("name") or "").strip()
    if not name:
        raise ValidationError({"name": "Give the project a name."})
    dup = Project.objects.using(alias).filter(name__iexact=name)
    if project:
        dup = dup.exclude(pk=project.pk)
    if dup.exists():
        raise ValidationError({"name": f'A project called "{name}" already exists in this company.'})
    mods = data.get("modules") or []
    if not isinstance(mods, list) or not mods:
        raise ValidationError({"modules": f"{name}: choose at least one module."})
    active = set(Module.objects.filter(is_active=True).values_list("code", flat=True))
    cleaned, seen = [], set()
    for m in mods:
        code = m.get("code") if isinstance(m, dict) else None
        if code not in active:
            raise ValidationError({"modules": f'{name}: unknown module "{code}".'})
        if code in seen:
            continue
        seen.add(code)
        cleaned.append({"code": code, **{c: bool(m.get(c, True)) for c in CAPS}})
    return {"name": name[:150], "description": (data.get("description") or "").strip()[:500], "modules": cleaned}


def save_project(company, data, project=None):
    """Create or update a project.

    data: {name, description, is_active?, modules: [...], admin_usernames: [...],
           new_admins: [{username, password, full_name, email}],
           screens?: {sub-module: {enabled, off_fields: [...]}}}   (database level)
    """
    alias = register_tenant_db(company.db_name)
    p = clean_project(data, alias, project)
    screens = iwms_tables.clean_screens(data.get("screens"))
    usernames = [u for u in (data.get("admin_usernames") or []) if isinstance(u, str)]
    existing = list(admins_qs(alias).filter(username__in=usernames))
    missing = {u.lower() for u in usernames} - {(s.username or "").lower() for s in existing}
    if missing:
        raise ValidationError({"admins": f'Admin not found: {", ".join(sorted(missing))}.'})
    new = clean_new_admins(data.get("new_admins"), alias)
    if not existing and not new:
        raise ValidationError({"admins": f'{p["name"]}: choose at least one admin to run this project.'})
    if new:
        try:
            user_room(company, alias, adding=len(new))
        except Exception as exc:
            raise ValidationError({"admins": str(getattr(exc, "detail", exc))})

    with tenant_context(alias), transaction.atomic(using=alias):
        if project is None:
            project = Project(name=p["name"], description=p["description"], company_id=company.unique_id)
        else:
            project.name, project.description = p["name"], p["description"]
            if "is_active" in data:
                project.is_active = bool(data["is_active"])
        project.save()
        admins = existing + create_admins(new, alias, company, project_id=project.pk)
        # Database level: modules → sub-modules → fields. Turning one off
        # turns off what needs it, turning one on brings what it needs; the
        # tables/columns follow in sync_company_modules().
        before = iwms_tables.project_states(alias).get(project.pk)
        final = iwms_tables.save_project_screens(
            alias, project, before, [m["code"] for m in p["modules"]], screens)
        p["modules"] = [m for m in p["modules"] if m["code"] in final] + [
            {"code": c, **{cap: True for cap in CAPS}}
            for c in sorted(final - {m["code"] for m in p["modules"]})]
        codes = [m["code"] for m in p["modules"]]
        now = timezone.now()
        ProjectModule.objects.using(alias).filter(project=project).exclude(module_code__in=codes).update(
            is_deleted=True, deleted_at=now, updated_at=now)
        for m in p["modules"]:
            ProjectModule.all_objects.using(alias).update_or_create(
                project=project, module_code=m["code"],
                defaults={**{c: m[c] for c in CAPS}, "is_deleted": False, "deleted_at": None,
                          "deleted_by_id": None, "is_active": True})
        ProjectAdmin.objects.using(alias).filter(project=project).exclude(
            staff_id__in=[a.pk for a in admins]).delete()
        for a in admins:
            ProjectAdmin.objects.using(alias).get_or_create(project=project, staff=a)
        # Staff can never keep more than the project now allows.
        for m in p["modules"]:
            for c in CAPS:
                if not m[c]:
                    ModulePermission.objects.using(alias).filter(
                        project=project, module_code=m["code"]).update(**{c: False})
    sync_company_modules(company)
    return project


def delete_project(company, project):
    """Soft delete: rows kept and marked deleted, staff access removed, name freed."""
    alias = register_tenant_db(company.db_name)
    now = timezone.now()
    with transaction.atomic(using=alias):
        module_tables.delete_project_rows(alias, project.pk)
        ProjectModule.objects.using(alias).filter(project=project).update(
            is_deleted=True, deleted_at=now, updated_at=now)
        ModulePermission.all_objects.using(alias).filter(project=project).delete()
        ProjectAdmin.objects.using(alias).filter(project=project).delete()
        Project.objects.using(alias).filter(pk=project.pk).update(
            is_deleted=True, is_active=False, deleted_at=now,
            name=f"{project.name}~deleted~{project.pk}"[:150])
    sync_company_modules(company)


def sync_company_modules(company):
    """The company follows its projects exactly:
    - CompanyModule rows exist for the modules used by a live project;
    - every used custom module has its table with the company's fields
      (removed fields' columns dropped); unused custom tables are dropped;
    - staff_access_configuration follows names and loses removed modules."""
    alias = register_tenant_db(company.db_name)
    codes = set(ProjectModule.objects.using(alias).filter(project__is_deleted=False)
                .values_list("module_code", flat=True))
    for module in Module.objects.filter(code__in=codes):
        cm, _ = CompanyModule.objects.get_or_create(company=company, module=module)
        if not module.is_builtin:
            module_tables.sync_table(alias, module.code, cm.effective_fields)
    for code in module_tables.module_codes(alias):
        if code not in codes:
            module_tables.drop_table(alias, code)
    CompanyModule.objects.filter(company=company).exclude(module__code__in=codes).delete()
    # Built-in IWMS modules: tables/columns follow the projects too.
    iwms_tables.sync_company(company, alias)
    refresh_staff_access(alias)


def projects_json(company):
    alias = register_tenant_db(company.db_name)
    modules = {m.code: m for m in Module.objects.all()}
    used = [m for m in modules.values() if CompanyModule.objects.filter(company=company, module=m).exists()]
    counts = project_counts(alias, used)
    staff_counts = dict(ModulePermission.objects.using(alias).filter(can_view=True)
                        .values("project_id").annotate(n=Count("staff", distinct=True))
                        .values_list("project_id", "n"))
    admins_by_project = {}
    for link in ProjectAdmin.objects.using(alias).select_related("staff"):
        admins_by_project.setdefault(link.project_id, []).append(link.staff)
    pms = {}
    for pm in ProjectModule.objects.using(alias).all():
        pms.setdefault(pm.project_id, []).append(pm)
    out = []
    for p in live_projects(alias).order_by("name"):
        mods = []
        for pm in pms.get(p.pk, []):
            m = modules.get(pm.module_code)
            mods.append({"code": pm.module_code, "name": m.name if m else pm.module_code,
                         "kind": m.kind if m else "custom",
                         "can_create": pm.can_create, "can_edit": pm.can_edit, "can_delete": pm.can_delete,
                         "records": counts.get(p.pk, {}).get(pm.module_code, 0)})
        out.append({
            "id": p.pk, "name": p.name, "description": p.description or "", "is_active": p.is_active,
            "created_at": p.created_at,
            "modules": sorted(mods, key=lambda m: m["name"]),
            "admins": [login_json(a) for a in admins_by_project.get(p.pk, []) if not a.is_deleted],
            "staff_with_access": staff_counts.get(p.pk, 0),
        })
    return out


def project_count(company):
    return live_projects(register_tenant_db(company.db_name)).count()

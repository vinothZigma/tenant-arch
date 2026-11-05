"""Keeps the company's `staff_access_configuration` table readable and correct.

Access is decided from the ids and can_* flags; this copies names (username,
staff name, login on/off, project, module, "View, Create" ...) into each row
so the table reads on its own in a DB tool, and deletes rows that give no
access (no flags, removed staff, deleted project, module taken out of the
project). Call refresh_staff_access(alias) after anything that changes staff,
projects, project modules, permissions or module names.
"""
from django.db import connections

from app.models.superadmin.staff_management.staffcreation import Staffcreation
from app.models.superadmin_masters.project import Project
from app.models.tenancy.tenant import ModulePermission, ProjectModule

TABLE = ModulePermission._meta.db_table
ACTIONS = ("view", "create", "edit", "delete")
COPIED = ("username", "staff_name", "login_enabled", "project_name", "module_name", "access")
COLUMN_ORDER = ("id", "staff_id", "username", "staff_name", "login_enabled", "project_id", "project_name",
                "module_code", "module_name", "can_view", "can_create", "can_edit", "can_delete", "access")


def access_words(row):
    granted = [a.capitalize() for a in ACTIONS if getattr(row, f"can_{a}")]
    return ", ".join(granted) if granted else "No access"


def refresh_staff_access(alias):
    _refresh_rows(alias)
    # Logins' IWMS role and project scope follow the same changes.
    from app.models.superadmin_masters.company import Company

    from .iwms_scope import sync_iwms_scopes

    company = Company.objects.filter(db_name=alias, is_deleted=False).first()
    if company is not None:
        sync_iwms_scopes(alias, company)


def _refresh_rows(alias):
    from app.models.tenancy.master import Module  # master DB

    rows = list(ModulePermission.all_objects.using(alias).all())
    if not rows:
        order_columns(alias)
        return
    staff = {s.pk: s for s in Staffcreation.objects.using(alias).filter(
        pk__in={r.staff_id for r in rows})}
    projects = {p.pk: p for p in Project.objects.using(alias).filter(pk__in={r.project_id for r in rows})}
    live_modules = set(ProjectModule.objects.using(alias).filter(project__is_deleted=False)
                       .values_list("project_id", "module_code"))
    module_names = dict(Module.objects.values_list("code", "name"))

    changed, gone = [], []
    for row in rows:
        person, project = staff.get(row.staff_id), projects.get(row.project_id)
        if (row.is_deleted or not any(getattr(row, f"can_{a}") for a in ACTIONS)
                or person is None or person.is_deleted or project is None or project.is_deleted
                or (row.project_id, row.module_code) not in live_modules):
            gone.append(row.pk)
            continue
        old = {f: getattr(row, f) for f in COPIED}
        row.username = person.username or ""
        row.staff_name = person.employee_name or ""
        row.login_enabled = bool(person.login_enabled)
        row.project_name = project.name
        row.module_name = module_names.get(row.module_code, row.module_code)
        row.access = access_words(row)
        if {f: getattr(row, f) for f in COPIED} != old:
            changed.append(row)
    if gone:
        ModulePermission.all_objects.using(alias).filter(pk__in=gone).delete()
    if changed:
        ModulePermission.all_objects.using(alias).bulk_update(changed, COPIED)
    order_columns(alias)


def order_columns(alias):
    """MySQL/MariaDB: who / project / module / access first, audit columns last."""
    conn = connections[alias]
    if conn.vendor != "mysql":
        return
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, column_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = %s ORDER BY ordinal_position", [TABLE])
        rows = cur.fetchall()
        defs = {name: (ctype, nullable) for name, ctype, nullable in rows}
        present = [name for name, _, _ in rows]
        wanted = [c for c in COLUMN_ORDER if c in defs] + [c for c in present if c not in COLUMN_ORDER]
        if wanted == present:
            return
        prev = wanted[0]
        for col in wanted[1:]:
            ctype, nullable = defs[col]
            cur.execute(f"ALTER TABLE `{TABLE}` MODIFY COLUMN `{col}` {ctype} "
                        f'{"NULL" if nullable == "YES" else "NOT NULL"} AFTER `{prev}`')
            prev = col

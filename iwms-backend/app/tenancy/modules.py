"""Counting records of any module, built-in or custom.

Built-in modules are existing IWMS models (Module.model_label) whose rows
carry project_id / is_active / is_deleted. Custom modules are module_<code>
tables (module_tables.py). Plan limits count rows that are active and not
deleted, across all projects of the company.
"""
from django.apps import apps
from django.db import models

from app.models.tenancy.tenant import ModuleLock

from . import module_tables

# Records of this module are the people who can get a staff login.
STAFF_MODULE = "staff-creations"


def builtin_model(module):
    app_label, model_name = module.model_label.split(".", 1)
    return apps.get_model(app_label, model_name)


def has_records(module):
    """Custom modules, and built-in modules backed by an IWMS table. Built-in
    modules like Dashboard or Reports have no records (no count, no limit)."""
    return not module.is_builtin or bool(module.model_label)


def builtin_qs(alias, module, counted=True):
    """Rows of a built-in module that are not deleted (and active when counted)."""
    model = builtin_model(module)
    qs = model.objects.using(alias).filter(is_deleted=False)
    if counted:
        qs = qs.filter(is_active=True)
    if module.code == STAFF_MODULE:
        # Admin and developer logins are accounts (user limit), not employees.
        qs = qs.filter(is_company_admin=False, is_developer=False)
    return qs


def project_counts(alias, modules, counted_only=False):
    """{project_id: {code: rows}} for the given Module objects."""
    out = module_tables.counts(alias, counted_only=counted_only)
    custom = {m.code for m in modules if not m.is_builtin}
    out = {pid: {c: n for c, n in mods.items() if c in custom} for pid, mods in out.items()}
    for m in modules:
        if not m.is_builtin or not m.model_label:
            continue
        rows = (builtin_qs(alias, m, counted=counted_only).values("project_id")
                .annotate(n=models.Count("pk")))
        for row in rows:
            out.setdefault(row["project_id"], {})[m.code] = row["n"]
    return out


def module_totals(alias, modules):
    """{code: counted rows across all projects}."""
    custom = module_tables.module_totals(alias)
    out = {}
    for m in modules:
        if m.is_builtin:
            out[m.code] = builtin_qs(alias, m).count() if m.model_label else 0
        else:
            out[m.code] = custom.get(m.code, 0)
    return out


def ensure_lock_row(alias, code):
    """Create the lock row outside the transaction that will take the lock."""
    ModuleLock.objects.using(alias).get_or_create(module_code=code)


def lock_and_count(alias, module):
    """Take the module's lock and return its counted rows. Call inside
    transaction.atomic(using=alias), after ensure_lock_row()."""
    ModuleLock.objects.using(alias).filter(module_code=module.code).update(counter=models.F("counter") + 1)
    if module.is_builtin:
        return builtin_qs(alias, module).count() if module.model_label else 0
    return module_tables.module_totals(alias).get(module.code, 0)

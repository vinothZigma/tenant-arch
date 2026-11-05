"""The shared catalog every company DB starts with.

Company tables in the master DB hold rows with no company (company_id NULL):
the screen catalog (main screens, user screens, actions), user types,
complaint categories, property types, ... In shared mode every company reads
them; in per_db mode each company DB needs its own copy. These master rows
are the template: copy_shared_rows() copies them into a company DB (new
companies, and existing ones moved with move_company_data).
"""
from contextlib import contextmanager

from django.apps import apps
from django.db import connections, transaction

from .catalog import is_tenant_model

BATCH = 500

# Definition tables whose no-company rows are the shared catalog. Other
# company tables' no-company rows are data, not templates.
SHARED_CATALOG_MODELS = frozenset({
    "mainscreentype", "mainscreen", "userscreen", "userscreenaction", "userscreencolumn",
    "usertype", "staffusertype", "contractorusertype",
    "property", "subproperty", "wastetype",
    "maincategory", "subcategory", "complaintcategory", "complaintsubcategory",
})


@contextmanager
def keep_timestamps(model):
    """bulk_create would stamp auto_now/auto_now_add fields with 'now'."""
    changed = []
    for field in model._meta.concrete_fields:
        if getattr(field, "auto_now", False) or getattr(field, "auto_now_add", False):
            changed.append((field, field.auto_now, field.auto_now_add))
            field.auto_now = field.auto_now_add = False
    try:
        yield
    finally:
        for field, auto_now, auto_now_add in changed:
            field.auto_now, field.auto_now_add = auto_now, auto_now_add


@contextmanager
def no_fk_checks(alias):
    conn = connections[alias]
    if conn.vendor == "mysql":
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
        try:
            yield
        finally:
            with conn.cursor() as cur:
                cur.execute("SET FOREIGN_KEY_CHECKS=1")
    else:
        yield


def copy_rows(model, source, alias):
    """bulk-copy `source` rows (keeping pks and timestamps) unless present."""
    existing = set(model._base_manager.using(alias).values_list("pk", flat=True))
    n, batch = 0, []
    with keep_timestamps(model):
        for row in source.iterator(chunk_size=BATCH):
            if row.pk in existing:
                continue
            row._state.db = alias
            batch.append(row)
            if len(batch) >= BATCH:
                model._base_manager.using(alias).bulk_create(batch)
                n += len(batch)
                batch = []
        if batch:
            model._base_manager.using(alias).bulk_create(batch)
            n += len(batch)
    return n


def shared_models():
    tables = set(connections["default"].introspection.table_names())
    return [
        m for m in apps.get_app_config("app").get_models()
        if is_tenant_model(m) and m._meta.managed and m._meta.db_table in tables
        and m._meta.model_name in SHARED_CATALOG_MODELS
        and "company_id" in {f.attname for f in m._meta.concrete_fields}
    ]


def copy_shared_rows(alias):
    """Copy the master DB's no-company rows of company tables into `alias`.
    Returns {table: rows copied}. Safe to run again."""
    out = {}
    with no_fk_checks(alias), transaction.atomic(using=alias):
        for model in shared_models():
            source = model._base_manager.using("default").filter(company_id__isnull=True)
            n = copy_rows(model, source, alias)
            if n:
                out[model._meta.db_table] = n
    return out

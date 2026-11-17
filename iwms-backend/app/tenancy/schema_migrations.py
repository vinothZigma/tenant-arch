"""Migrations from the super admin console (Companies → Migrate).

New IWMS modules come with Django migrations. A new company's DB is migrated
when it is created (services.provision_company); existing company DBs get
new migrations here, or with `manage.py migrate_tenants`.

The master DB goes first: new modules usually add master rows/tables too
(the module catalog via seed_defaults), and company DBs only build company
tables (the router filters the rest).
"""
import logging
import threading
import traceback
from contextlib import contextmanager

from django.core.management import call_command
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from rest_framework.exceptions import APIException

from .context import tenant_context
from .db import is_sqlite, migrate_tenant_db, register_tenant_db, tenant_db_exists, create_tenant_db
from .router import MASTER_DB

logger = logging.getLogger(__name__)

_local_lock = threading.Lock()
LOCK_NAME = "iwms_console_migrate"


class MigrationBusy(APIException):
    status_code = 409
    default_detail = "A migration is already running. Try again in a moment."


@contextmanager
def _migration_lock():
    """One migration at a time, across every server worker (MySQL GET_LOCK)."""
    if not _local_lock.acquire(blocking=False):
        raise MigrationBusy()
    try:
        if is_sqlite():
            yield
            return
        with connections[MASTER_DB].cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, 0)", [LOCK_NAME])
            if cursor.fetchone()[0] != 1:
                raise MigrationBusy()
        try:
            yield
        finally:
            with connections[MASTER_DB].cursor() as cursor:
                cursor.execute("SELECT RELEASE_LOCK(%s)", [LOCK_NAME])
    finally:
        _local_lock.release()


def pending(alias):
    """Migrations not yet applied to this DB, e.g. ["app.0142_new_module"]."""
    executor = MigrationExecutor(connections[alias])
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    return [f"{m.app_label}.{m.name}" for m, backwards in plan if not backwards]


def company_pending(company):
    """Pending migrations of a company DB; None when its DB is missing."""
    if not company.db_name or not tenant_db_exists(company.db_name):
        return None
    alias = register_tenant_db(company.db_name)
    with tenant_context(alias):
        return pending(alias)


def status(companies):
    """{"master": [...], "companies": {id: {"pending": [...] | None, "error"?}}}."""
    out = {"master": pending(MASTER_DB), "companies": {}}
    for company in companies:
        try:
            out["companies"][str(company.pk)] = {"pending": company_pending(company)}
        except Exception as exc:
            logger.exception("Migration status failed for %s", company.slug)
            out["companies"][str(company.pk)] = {"pending": None, **_error(exc)}
    return out


def _migrate_master():
    todo = pending(MASTER_DB)
    if todo:
        call_command("migrate", database=MASTER_DB, verbosity=0, interactive=False)
        # New built-in modules / plan limits for them (get_or_create: safe).
        call_command("seed_defaults", verbosity=0)
    return todo


def _error(exc):
    """What the console shows for a failed migration: the message and the
    traceback, so the failing migration/statement can be found."""
    return {"error": f"{type(exc).__name__}: {exc}",
            "traceback": "".join(traceback.format_exception(exc))}


def _migrate_company(company):
    """{"company", "name", "status": created|migrated|up_to_date|failed, "applied",
    "pending"?, "error"?, "traceback"?}."""
    out = {"company": str(company.pk), "name": company.name, "applied": []}
    try:
        if not tenant_db_exists(company.db_name):
            create_tenant_db(company.db_name)
            from .shared_rows import copy_shared_rows

            copy_shared_rows(register_tenant_db(company.db_name))
            out["status"] = "created"
            return out
        todo = company_pending(company) or []
        out["pending"] = todo
        if todo:
            migrate_tenant_db(company.db_name)
        out["applied"] = todo
        out["status"] = "migrated" if todo else "up_to_date"
    except Exception as exc:  # one broken company must not stop the rest
        logger.exception("Migration failed for %s", company.slug)
        out["status"] = "failed"
        out["applied"] = []
        try:  # which of the pending ones did get applied before the error
            left = set(company_pending(company) or [])
            out["applied"] = [m for m in out.get("pending", []) if m not in left]
        except Exception:
            pass
        out.update(_error(exc))
    return out


def run(companies):
    """Migrate master (if behind) then each company. Never raises: errors come
    back in the result (master_error / each company's status "failed").
    When the master DB fails, company DBs are left alone (they may need it)."""
    with _migration_lock():
        try:
            master = _migrate_master()
        except Exception as exc:
            logger.exception("Master DB migration failed")
            return {"master_applied": [], "master_failed": True, **{f"master_{k}": v for k, v in _error(exc).items()},
                    "companies": [{"company": str(c.pk), "name": c.name, "status": "skipped", "applied": [],
                                   "error": "Not migrated: the master database migration failed first."}
                                  for c in companies]}
        results = [_migrate_company(c) for c in companies]
    return {"master_applied": master, "master_failed": False, "companies": results}

"""Create, register, migrate and drop company databases.

The connection alias of a company DB is its database name.
"""
import copy

from django.conf import settings
from django.core.management import call_command
from django.db import connections

from .context import tenant_context
from .router import MASTER_DB
from .slugs import validate_db_name


def is_sqlite():
    return connections.databases[MASTER_DB]["ENGINE"].endswith("sqlite3")


def sqlite_path(db_name):
    return settings.TENANT_SQLITE_DIR / f"{validate_db_name(db_name)}.sqlite3"


def register_tenant_db(db_name):
    """Make `db_name` usable as a connection alias (idempotent). Returns the alias."""
    validate_db_name(db_name)
    if db_name not in connections.databases:
        # Copy the fully-populated master config, then point it at the company DB.
        cfg = copy.deepcopy(connections.databases[MASTER_DB])
        if is_sqlite():
            settings.TENANT_SQLITE_DIR.mkdir(parents=True, exist_ok=True)
            cfg["NAME"] = str(sqlite_path(db_name))
        else:
            cfg["NAME"] = db_name
        cfg.setdefault("TEST", {})["NAME"] = cfg["NAME"]
        connections.databases[db_name] = cfg
    return db_name


def unregister_tenant_db(db_name):
    if db_name in connections.databases:
        connections[db_name].close()
        del connections.databases[db_name]
        try:
            del connections[db_name]
        except (AttributeError, KeyError):
            pass


def tenant_db_exists(db_name):
    validate_db_name(db_name)
    if is_sqlite():
        return sqlite_path(db_name).exists()
    with connections[MASTER_DB].cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s", [db_name]
        )
        return cursor.fetchone() is not None


def _master_charset():
    """(charset, collation) of the master DB, so company DBs match it."""
    with connections[MASTER_DB].cursor() as cursor:
        cursor.execute(
            "SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME "
            "FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = DATABASE()"
        )
        row = cursor.fetchone()
    return row or ("utf8mb4", "utf8mb4_unicode_ci")


def migrate_tenant_db(db_name, verbosity=0):
    """Apply all migrations to a company DB; the router builds only company tables.

    Runs inside tenant_context so post_migrate hooks (screen-column sync,
    audit triggers) work on this company's DB.
    """
    alias = register_tenant_db(db_name)
    from . import iwms_tables

    built = "tenancy_project_module" in iwms_tables.existing_tables(alias)
    with tenant_context(alias):
        if built:
            # Tables/columns dropped for unused modules/fields come back
            # first, so the migrations find what they expect.
            iwms_tables.restore_all(alias)
        # run_syncdb only matters for apps without migrations (the test
        # settings disable them); the router still filters which tables.
        call_command(
            "migrate", database=alias, verbosity=verbosity, interactive=False, run_syncdb=True
        )
        if built:
            from app.models.superadmin_masters.company import Company

            company = Company.objects.filter(db_name=db_name, is_deleted=False).first()
            if company is not None:
                iwms_tables.sync_company(company, alias)
    return alias


def create_tenant_db(db_name, verbosity=0):
    """Create the physical database (if missing) and build its tables."""
    validate_db_name(db_name)
    if not is_sqlite():
        charset, collation = _master_charset()
        # charset/collation come from information_schema, not user input.
        with connections[MASTER_DB].cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
                f"CHARACTER SET {charset} COLLATE {collation}"
            )
    return migrate_tenant_db(db_name, verbosity=verbosity)


def drop_tenant_db(db_name):
    """Drop the database. Callers must back it up first (see backup.py)."""
    validate_db_name(db_name)
    unregister_tenant_db(db_name)
    if is_sqlite():
        sqlite_path(db_name).unlink(missing_ok=True)
    else:
        with connections[MASTER_DB].cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{db_name}`")


def tenant_table_names(db_name):
    alias = register_tenant_db(db_name)
    return set(connections[alias].introspection.table_names())

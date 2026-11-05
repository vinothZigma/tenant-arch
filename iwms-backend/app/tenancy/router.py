"""Keeps master tables in 'default' and company tables in company DBs.

TENANCY_MODE = "shared" (current default)
    Reads/writes are not routed: everything uses 'default' as before.
    Migrations still build company tables in 'default' (they live there until
    the data moves) AND in every company DB.

TENANCY_MODE = "per_db"
    Company tables go to the DB selected with tenant_context(); with none
    selected the query raises TenantNotSelected instead of quietly reading
    the master DB. `.using(alias)` always wins over the router.
    Company tables are no longer created in 'default'.

Data migrations (RunPython/RunSQL) that touch company tables must say so,
e.g. RunPython(fn, hints={"model_name": "staffcreationofficedetails"}),
otherwise they only run on the master DB.
"""
from django.conf import settings

from .catalog import is_master_model, is_tenant_model
from .context import TenantNotSelected, get_current_tenant_db

MASTER_DB = "default"


def per_db_mode():
    return getattr(settings, "TENANCY_MODE", "shared") == "per_db"


class TenantRouter:
    def _route(self, model, hints):
        if not per_db_mode():
            return None  # shared mode: Django's normal choice ('default')
        if not is_tenant_model(model):
            return MASTER_DB
        # Related objects follow the row they hang off (e.g. assigning a FK
        # to a row loaded with .using(alias)).
        instance = hints.get("instance")
        if instance is not None and getattr(instance, "_state", None) and instance._state.db:
            return instance._state.db
        alias = get_current_tenant_db()
        if alias is None:
            raise TenantNotSelected(
                f"{model.__name__} is company data, but no company database is "
                "selected. Wrap the code in tenant_context(alias) or use .using(alias)."
            )
        return alias

    def db_for_read(self, model, **hints):
        return self._route(model, hints)

    def db_for_write(self, model, **hints):
        return self._route(model, hints)

    def allow_relation(self, obj1, obj2, **hints):
        # Rows from different databases can never be related.
        return obj1._state.db == obj2._state.db

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if model_name is None:
            # Model-less operations (RunSQL/RunPython without hints).
            return db == MASTER_DB
        if is_master_model(app_label, model_name):
            return db == MASTER_DB
        if db == MASTER_DB:
            # Company tables stay in the master DB only while it still holds
            # all companies' data.
            return not per_db_mode()
        return True

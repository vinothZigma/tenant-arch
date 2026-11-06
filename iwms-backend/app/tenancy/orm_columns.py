"""Let IWMS's models work on company DBs where some tables/columns were
dropped (app/tenancy/iwms_tables.py: a module or field no project uses).

  table    a SELECT touching a dropped module table returns no rows (like
           .none()): other modules, logins and dashboards that read it see
           "nothing there" instead of crashing;

  SELECT   dropped columns are left out (treated like .defer());
  read     a dropped field's attribute reads as the field's default;
  INSERT   dropped columns are left out;
  UPDATE   dropped columns are left out;
  audit    writes to a dropped audit-log table are skipped; deletes from
           any dropped table delete nothing.

Code that names a dropped column itself (filter/order_by/values on it) still
fails; exceptions.py turns that into a readable 409.
"""
from django.core.exceptions import EmptyResultSet
from django.db.models import F
from django.db.models.query_utils import DeferredAttribute
from django.db.models.sql import compiler as sql

from .router import MASTER_DB

_installed = False


def _dropped(alias):
    if not alias or alias == MASTER_DB:
        return frozenset()
    from .iwms_tables import dropped_columns

    return dropped_columns(alias)


def _missing(alias):
    if not alias or alias == MASTER_DB:
        return frozenset()
    from .iwms_tables import missing_tables

    return missing_tables(alias)


def _is_dropped(dropped, field):
    return (field.model._meta.db_table, field.column) in dropped


def install():
    global _installed
    if _installed:
        return
    _installed = True

    select_as_sql = sql.SQLCompiler.as_sql

    def patched_select_as_sql(self, *args, **kwargs):
        missing = _missing(self.using)
        if missing:
            query = self.query
            for alias, join in query.alias_map.items():
                if join.table_name in missing and (query.alias_refcount.get(alias) or alias == query.base_table):
                    if self.select is None:
                        self.setup_query()  # iterables read select/klass_info even when empty
                    raise EmptyResultSet
        return select_as_sql(self, *args, **kwargs)

    sql.SQLCompiler.as_sql = patched_select_as_sql

    get_default_columns = sql.SQLCompiler.get_default_columns

    def patched_get_default_columns(self, select_mask, start_alias=None, opts=None, from_parent=None):
        dropped = _dropped(self.using)
        if dropped:
            meta = opts or self.query.get_meta()
            gone = [f for f in meta.concrete_fields if _is_dropped(dropped, f)]
            if gone:
                if select_mask:
                    mask = {k: v for k, v in select_mask.items() if k not in gone} or {meta.pk: {}}
                else:
                    mask = {f: {} for f in meta.concrete_fields if f not in gone}
                select_mask = mask
        return get_default_columns(self, select_mask, start_alias, opts, from_parent)

    sql.SQLCompiler.get_default_columns = patched_get_default_columns

    insert_as_sql = sql.SQLInsertCompiler.as_sql

    def patched_insert_as_sql(self, *args, **kwargs):
        dropped = _dropped(self.using)
        if dropped and self.query.fields:
            self.query.fields = [f for f in self.query.fields if not _is_dropped(dropped, f)]
        return insert_as_sql(self, *args, **kwargs)

    sql.SQLInsertCompiler.as_sql = patched_insert_as_sql

    update_as_sql = sql.SQLUpdateCompiler.as_sql

    def patched_update_as_sql(self, *args, **kwargs):
        dropped = _dropped(self.using)
        if dropped and self.query.values:
            kept = [v for v in self.query.values if not _is_dropped(dropped, v[0])]
            if not kept:
                # Nothing left to set: a no-op that still matches the row, so
                # Model.save() doesn't mistake it for a missing row and INSERT.
                pk = self.query.get_meta().pk
                kept = [(pk, None, F(pk.attname))]
            self.query.values = kept
        return update_as_sql(self, *args, **kwargs)

    sql.SQLUpdateCompiler.as_sql = patched_update_as_sql

    deferred_get = DeferredAttribute.__get__

    def patched_deferred_get(self, instance, cls=None):
        if instance is not None and self.field.attname not in instance.__dict__:
            dropped = _dropped(instance._state.db)
            if dropped and _is_dropped(dropped, self.field):
                value = self.field.get_default()
                instance.__dict__[self.field.attname] = value
                return value
        return deferred_get(self, instance, cls)

    DeferredAttribute.__get__ = patched_deferred_get

    # Audit logs of a company that turned Audits off: writes are skipped.
    from .iwms_tables import AUDIT_TABLES

    insert_execute = sql.SQLInsertCompiler.execute_sql

    def patched_insert_execute(self, returning_fields=None):
        table = self.query.get_meta().db_table
        if table in AUDIT_TABLES and table in _missing(self.using):
            if not returning_fields:
                return []
            return [tuple(None for _ in returning_fields) for _ in self.query.objs]
        return insert_execute(self, returning_fields)

    sql.SQLInsertCompiler.execute_sql = patched_insert_execute

    update_execute = sql.SQLUpdateCompiler.execute_sql

    def patched_update_execute(self, result_type):
        table = self.query.get_meta().db_table
        if table in AUDIT_TABLES and table in _missing(self.using):
            return 0
        return update_execute(self, result_type)

    sql.SQLUpdateCompiler.execute_sql = patched_update_execute

    delete_execute = sql.SQLDeleteCompiler.execute_sql

    def patched_delete_execute(self, *args, **kwargs):
        # Nothing to delete in a dropped table.
        if self.query.get_meta().db_table in _missing(self.using):
            return 0
        return delete_execute(self, *args, **kwargs)

    sql.SQLDeleteCompiler.execute_sql = patched_delete_execute

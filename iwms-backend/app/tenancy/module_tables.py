"""Each custom module gets its own real table in the company DB.

    module_assets(id, project_id, <one typed column per field...>,
                  is_active, is_deleted, created_at, updated_at, deleted_at,
                  created_by_id, updated_by_id, deleted_by_id)

Rows are soft-deleted. *_by_id hold the staff_unique_id of the company user
(NULL = super admin / system). Columns follow the company's fields for the
module (CompanyModule.effective_fields): a new field adds a column, a type
change alters it, a removed field drops it (only on an explicit field
change), and a module no longer used by any project has its table dropped.
ModuleTable / ModuleColumn record what exists.

Model classes are built at runtime in an isolated app registry so the ORM and
the schema editor work on MySQL/MariaDB and SQLite alike.
"""
import datetime
import re

from django.apps.registry import Apps
from django.db import DatabaseError, connections, models, transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from app.models.tenancy.tenant import ModuleColumn, ModuleTable

SYSTEM_COLUMNS = (
    "id", "project_id", "is_active", "is_deleted", "created_at", "updated_at", "deleted_at",
    "created_by_id", "updated_by_id", "deleted_by_id",
)
# Bump when built-in columns are added; older tables are upgraded on next use
# (see upgrade_system_columns). Version 1 is the first IWMS version.
SYSTEM_VERSION = 1
_ADDED_AFTER = {}  # {version: (column, ...)} for future upgrades
COUNTED = {"is_deleted": False, "is_active": True}


def table_name(code):
    return "module_" + re.sub(r"[^a-z0-9_]", "_", code.lower())


def _field(ftype):
    if ftype == "textarea":
        return models.TextField(null=True, blank=True)
    if ftype == "email":
        return models.CharField(max_length=254, null=True, blank=True)
    if ftype == "number":
        return models.FloatField(null=True, blank=True)
    if ftype == "date":
        return models.DateField(null=True, blank=True)
    if ftype == "boolean":
        return models.BooleanField(null=True, blank=True)
    return models.CharField(max_length=255, null=True, blank=True)  # text


_seq = [0]


def build_model(code, columns):
    """Model for module_<code> with {column: type} plus the system columns."""
    _seq[0] += 1
    attrs = {
        "__module__": __name__,
        "Meta": type("Meta", (), {"db_table": table_name(code), "app_label": "tenant_modules", "apps": Apps()}),
        "id": models.BigAutoField(primary_key=True),
        "project_id": models.CharField(max_length=30, db_index=True),
    }
    for name, ftype in columns.items():
        f = _field(ftype)
        f.db_column = name
        attrs["f_" + name] = f  # prefixed so field names can't clash with model attributes
    attrs.update({
        "is_active": models.BooleanField(default=True),
        "is_deleted": models.BooleanField(default=False, db_index=True),
        "created_at": models.DateTimeField(auto_now_add=True),
        "updated_at": models.DateTimeField(auto_now=True),
        "deleted_at": models.DateTimeField(null=True, blank=True),
        "created_by_id": models.CharField(max_length=50, null=True, blank=True),
        "updated_by_id": models.CharField(max_length=50, null=True, blank=True),
        "deleted_by_id": models.CharField(max_length=50, null=True, blank=True),
    })
    return type(f'Module_{re.sub(r"[^A-Za-z0-9]", "_", code)}_{_seq[0]}', (models.Model,), attrs)


def _columns(alias, code):
    table = ModuleTable.objects.using(alias).filter(module_code=code).first()
    if not table:
        return None
    return {c.name: c.type for c in ModuleColumn.objects.using(alias).filter(table=table)}


def upgrade_system_columns(alias, code):
    """Add built-in columns introduced after a table was created."""
    table = ModuleTable.objects.using(alias).filter(module_code=code).first()
    if not table or table.system_version >= SYSTEM_VERSION:
        return
    conn = connections[alias]
    existing = {c.name for c in conn.introspection.get_table_description(conn.cursor(), table_name(code))}
    model = build_model(code, _columns(alias, code))
    for version in sorted(v for v in _ADDED_AFTER if v > table.system_version):
        for name in _ADDED_AFTER[version]:
            if name not in existing:
                with conn.schema_editor() as se:
                    se.add_field(model, model._meta.get_field(name))
    table.system_version = SYSTEM_VERSION
    table.save(using=alias, update_fields=["system_version"])


def sync_table(alias, code, fields, drop_removed=True):
    """Create module_<code> or bring its columns in line with `fields`.

    drop_removed=False (normal record reads/writes) never drops data.
    """
    wanted = {f["name"]: f.get("type", "text") for f in fields}
    clash = set(wanted) & set(SYSTEM_COLUMNS)
    if clash:
        raise ValidationError({"fields": f'Field name(s) {", ".join(sorted(clash))} are reserved.'})
    current = _columns(alias, code)
    conn = connections[alias]

    if current is None:
        model = build_model(code, wanted)
        if table_name(code) not in conn.introspection.table_names():
            with conn.schema_editor() as se:
                se.create_model(model)
        with transaction.atomic(using=alias):
            table = ModuleTable.objects.using(alias).create(module_code=code, system_version=SYSTEM_VERSION)
            for name, ftype in wanted.items():
                ModuleColumn.objects.using(alias).create(table=table, name=name, type=ftype, active=True)
        order_columns(alias, code, list(wanted))
        return

    upgrade_system_columns(alias, code)
    table = ModuleTable.objects.using(alias).get(module_code=code)
    after = {**current, **wanted}
    before_model, after_model = build_model(code, current), build_model(code, after)
    for name, ftype in wanted.items():
        new_field = after_model._meta.get_field("f_" + name)
        try:
            if name not in current:
                with conn.schema_editor() as se:
                    se.add_field(after_model, new_field)
            elif current[name] != ftype:
                with conn.schema_editor() as se:
                    se.alter_field(after_model, before_model._meta.get_field("f_" + name), new_field)
        except DatabaseError:
            raise ValidationError({"fields": (
                f'Could not change "{name}" to {ftype}: some existing values do not fit. '
                "Fix or clear those values first, or add a new field instead.")})
        ModuleColumn.objects.using(alias).update_or_create(
            table=table, name=name, defaults={"type": ftype, "active": True})
    removed = [n for n in current if n not in wanted]
    if not drop_removed:
        ModuleColumn.objects.using(alias).filter(table=table, name__in=removed).update(active=False)
        order_columns(alias, code, list(wanted))
        return
    for name in removed:
        with conn.schema_editor() as se:
            se.remove_field(before_model, before_model._meta.get_field("f_" + name))
        ModuleColumn.objects.using(alias).filter(table=table, name=name).delete()
    order_columns(alias, code, list(wanted))


def order_columns(alias, code, field_names):
    """MySQL/MariaDB: id, project_id, fields in order, then audit columns."""
    conn = connections[alias]
    if conn.vendor != "mysql":
        return
    table = table_name(code)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, column_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = %s ORDER BY ordinal_position", [table])
        rows = cur.fetchall()
        defs = {name: (ctype, nullable) for name, ctype, nullable in rows}
        present = [name for name, _, _ in rows]
        audit_cols = [c for c in SYSTEM_COLUMNS if c not in ("id", "project_id") and c in defs]
        data_cols = [n for n in field_names if n in defs] + \
                    [n for n in present if n not in field_names and n not in SYSTEM_COLUMNS]
        wanted = [c for c in ("id", "project_id") if c in defs] + data_cols + audit_cols
        if wanted == present:
            return
        prev = "project_id"
        for col in data_cols + audit_cols:
            ctype, nullable = defs[col]
            cur.execute(f"ALTER TABLE `{table}` MODIFY COLUMN `{col}` {ctype} "
                        f'{"NULL" if nullable == "YES" else "NOT NULL"} AFTER `{prev}`')
            prev = col


def drop_table(alias, code):
    """Drop module_<code> with all its rows (no project uses the module any more)."""
    columns = _columns(alias, code)
    if columns is None:
        return
    model = build_model(code, columns)
    conn = connections[alias]
    if table_name(code) in conn.introspection.table_names():
        with conn.schema_editor() as se:
            se.delete_model(model)
    ModuleTable.objects.using(alias).filter(module_code=code).delete()


def module_codes(alias):
    return list(ModuleTable.objects.using(alias).values_list("module_code", flat=True))


def data_summary(alias):
    """{code: {'rows': n, 'columns': {column: rows with a value}}} for drop warnings."""
    out = {}
    for code in module_codes(alias):
        columns = _columns(alias, code) or {}
        qs = build_model(code, columns).objects.using(alias)
        out[code] = {"rows": qs.count(),
                     "columns": {c: qs.exclude(**{"f_" + c: None}).count() for c in columns}}
    return out


class ModuleStore:
    """Read/write rows of one custom module's table with the company's fields."""

    def __init__(self, alias, code, fields):
        self.alias, self.code, self.fields = alias, code, fields
        upgrade_system_columns(alias, code)
        columns = _columns(alias, code)
        if columns is None or any(
            f["name"] not in columns or columns[f["name"]] != f.get("type", "text") for f in fields
        ):
            sync_table(alias, code, fields, drop_removed=False)
            columns = _columns(alias, code)
        self.model = build_model(code, columns)

    def qs(self):
        return self.model.objects.using(self.alias).filter(is_deleted=False)

    def deleted_qs(self):
        return self.model.objects.using(self.alias).filter(is_deleted=True)

    def to_json(self, row):
        data = {}
        for f in self.fields:
            v = getattr(row, "f_" + f["name"], None)
            if isinstance(v, datetime.date):
                v = v.isoformat()
            elif isinstance(v, float) and v.is_integer():
                v = int(v)
            elif f.get("type") == "boolean":
                v = bool(v)
            data[f["name"]] = v
        return {
            "id": row.id, "project_id": row.project_id, "data": data,
            "is_active": row.is_active, "is_deleted": row.is_deleted,
            "created_at": row.created_at, "updated_at": row.updated_at, "deleted_at": row.deleted_at,
            "created_by_id": row.created_by_id, "updated_by_id": row.updated_by_id,
            "deleted_by_id": row.deleted_by_id,
        }

    def _assign(self, row, data):
        for f in self.fields:
            setattr(row, "f_" + f["name"], data.get(f["name"]))

    def create(self, project_id, data, user_id=None, is_active=True):
        row = self.model(project_id=project_id, created_by_id=user_id, updated_by_id=user_id,
                         is_active=is_active)
        self._assign(row, data)
        row.save(using=self.alias)
        return row

    def update(self, row, data, user_id=None, is_active=None):
        self._assign(row, data)
        row.updated_by_id = user_id
        if is_active is not None:
            row.is_active = bool(is_active)
        row.save(using=self.alias)
        return row

    def soft_delete(self, row, user_id=None):
        row.is_deleted, row.deleted_at, row.deleted_by_id = True, timezone.now(), user_id
        row.save(using=self.alias, update_fields=["is_deleted", "deleted_at", "deleted_by_id", "updated_at"])
        return row

    def restore(self, row, user_id=None):
        row.is_deleted, row.deleted_at, row.deleted_by_id, row.updated_by_id = False, None, None, user_id
        row.save(using=self.alias,
                 update_fields=["is_deleted", "deleted_at", "deleted_by_id", "updated_by_id", "updated_at"])
        return row


def _plain_model(alias, code):
    upgrade_system_columns(alias, code)
    return build_model(code, {})


def counts(alias, counted_only=False):
    """{project_id: {code: rows}} over every custom module table."""
    out = {}
    flt = COUNTED if counted_only else {"is_deleted": False}
    for code in module_codes(alias):
        m = _plain_model(alias, code)
        for row in m.objects.using(alias).filter(**flt).values("project_id").annotate(n=models.Count("id")):
            out.setdefault(row["project_id"], {})[code] = row["n"]
    return out


def module_totals(alias):
    """{code: counted rows across all projects} for custom modules."""
    return {code: _plain_model(alias, code).objects.using(alias).filter(**COUNTED).count()
            for code in module_codes(alias)}


def locked_count(alias, code):
    """Counted rows, locked (call inside a transaction)."""
    if code not in module_codes(alias):
        return 0
    m = _plain_model(alias, code)
    return len(m.objects.using(alias).select_for_update().filter(**COUNTED).values_list("id", flat=True))


def delete_project_rows(alias, project_id, user_id=None):
    now = timezone.now()
    for code in module_codes(alias):
        _plain_model(alias, code).objects.using(alias).filter(project_id=project_id, is_deleted=False).update(
            is_deleted=True, deleted_at=now, deleted_by_id=user_id, updated_at=now)


def check_room(alias, code, limit, message):
    """Raise unless one more counted row fits. Call inside a transaction."""
    if limit is not None and locked_count(alias, code) >= limit:
        raise PermissionDenied(message)

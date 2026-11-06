"""Database level: which IWMS tables and columns exist in a company DB.

Set per project in Console → Companies → (company) → Edit project, as a tree:
module → sub-module (IWMS screen; grouped ones like "Daily Trip Plan" shown
together) → fields. The company DB holds exactly what its live projects use:

  * a sub-module no project uses   -> its tables are DROPPED (rows gone);
    used again                     -> its tables are CREATED (empty);
  * a field no project uses        -> its column is DROPPED;
    used again                     -> the column is ADDED back (empty).

A module's checkbox is all of its sub-modules. Sub-modules depend on each
other's data (a daily trip needs its trip plan): turning one off turns off
what needs it, turning one on turns on what it needs (cascade_screens()).

(What admins may *do* and *see* in the UI is separate: Permissions & fields,
app/tenancy/permissions.py — it never changes the database.)

Never dropped:
  * core modules — login, staff, roles, screens, audits and company/project
    tables are needed on every request;
  * nothing is shared between companies: lookups (country/state, fuel,
    vehicle types, complaint lookups, the API audit trail) are company tables
    too, copied from the master DB's template when created (shared_rows.py).

Tables and columns are built from the migration state the company DB is at
(not from the current model code), so later `migrate_tenants` runs still
line up; migrate_tenant_db restores everything first and re-applies the
drops afterwards. Queries on dropped tables read as empty, dropped columns
are skipped (orm_columns.py).
"""
import logging
import time

from django.apps import apps
from django.db import connections, transaction

from app.utils import permission_catalog as catalog

from .catalog import is_tenant_model

logger = logging.getLogger(__name__)

# Every other table has company_id / project_id: Company & Project is the
# only core module.
CORE_MODULES = frozenset({"superadmin-masters"})

# Not offered to companies at all: what Screen Management does (which screens
# and fields a company gets) is the super admin's Permissions & fields page,
# and company admins give their staff access through Staff Access
# Configuration. Its tables stay (SYSTEM_TABLES): every sign-in reads them.
HIDDEN_MODULES = frozenset({"screen-managements"})

# Tables every sign-in and permission check reads or writes: kept even when
# their module is unticked (dropping them would lock everyone out). The
# company admins themselves are staff rows; the screen catalog and grants
# build every login's menu; the login gives each admin a user type/role.
SYSTEM_TABLES = frozenset({
    "app_project",
    "app_staffcreationofficedetails", "app_account",
    "staff_access_configuration",   # the console's own staff grants (tenancy ModulePermission)
    "app_usertype", "app_staffusertype",
    "app_mainscreentype", "app_mainscreen", "app_userscreen", "app_userscreenaction", "app_userscreencolumn",
    "app_companyuserscreenpermission", "app_companyuserscreencolumnpermission",
    "app_appmodule",   # mobile app modules: the login's app gate reads them
})

# Written from all over the code (login, middleware, trip screens): when
# dropped, writes to them are skipped instead of failing (orm_columns.py).
AUDIT_TABLES = frozenset({
    "app_loginaudit", "permission_audit_logs", "static_route_audit_logs", "db_change_audit",
    "app_auditlog", "staff_template_audit_logs", "common_audit", "complaint_audit_logs",
})

# Company-DB tables of each sub-module; the first is the table of its form,
# the others its child tables (dropped and created with it).
SCREEN_TABLES = {
    # Staff Management (the staff and access tables themselves are SYSTEM_TABLES)
    "department-masters": ["app_department"],
    "designation-masters": ["app_designation"],
    "staffcreation": ["app_staffcreationofficedetails", "app_staffpersonaldetails", "app_account"],
    # IWMS's own Data Scope; without it a company admin's projects come from
    # the console's project admins (screen_grants.admin_project_scope).
    "staff-access-configuration": ["app_staffaccessconfiguration", "app_staffaccessconfigurationpermission"],
    # Role Management
    "user-type": ["app_usertype"],
    "staffusertypes": ["app_staffusertype"],
    "contractorusertypes": ["app_contractorusertype"],
    "project-staff-hierarchy": ["app_projectstaffhierarchy"],
    # Screen Management (all kept: SYSTEM_TABLES)
    "mainscreentype": ["app_mainscreentype"],
    "mainscreens": ["app_mainscreen"],
    "userscreens": ["app_userscreen", "app_userscreencolumn"],
    "userscreen-action": ["app_userscreenaction"],
    "companywisescreenpermissions": ["app_companyuserscreenpermission", "app_companyuserscreencolumnpermission"],
    "app-modules": ["app_appmodule"],
    # Common Masters (each company has its own copy)
    "continents": ["app_continent"],
    "countries": ["app_country"],
    "states": ["app_state"],
    # Audits
    "audit-dashboard": ["app_auditlog", "staff_template_audit_logs"],
    "common-audit": ["common_audit"],
    "complaint-audit": ["complaint_audit_logs"],
    "login-audit": ["app_loginaudit"],
    "permission-audit": ["permission_audit_logs"],
    "static-route-audit": ["static_route_audit_logs"],
    "db-change-audit": ["db_change_audit"],
    # Location Masters / Leader Management
    "districts": ["app_district"],
    "cities": ["app_city"],
    "zones": ["app_zone"],
    "wards": ["app_ward"],
    "panchayat": ["app_panchayat", "app_blockpanchayatunion"],
    "panchayat-leaders": ["app_panchayatleaderlogin"],
    "district-leaders": ["app_districtleaderlogin"],
    "plants": ["app_plant"],
    # Waste types
    "properties": ["app_property"],
    "subproperties": ["app_subproperty"],
    "bins": ["app_bins"],
    "waste type": ["app_wastetype", "app_wastecollectionmain", "app_wastecollectionsub"],
    # Transport Masters
    "vehicle-type": ["app_vehicletypecreation"],
    "vehicle-creation": ["app_vehiclecreation", "app_vehicledocumenthistory"],
    "fuels": ["app_fuel"],
    # Customer Masters
    "customercreations": ["app_customercreation", "app_passwordresetotp"],
    "customer-access-configuration": ["app_customeraccessconfiguration"],
    # Complaint Masters
    "types": ["app_maincategory"],
    "categories": ["app_complaintcategory", "app_complaintmodule", "app_complaintpriority"],
    "subcategories": ["app_complaintsubcategory", "app_subcategory"],
    "sla-rules": ["app_complaintslarule", "app_complaintslaescalationlevel", "app_complaintroutingrule"],
    # Complaint Management
    "tickets": [
        "app_complaintticket", "app_complaint", "app_complaintsource", "app_complaintlanguage",
        "app_complaintstatus", "app_complaintattachment", "app_complaintcomment",
        "app_complaintstatushistory", "app_complaintassignmenthistory", "app_complaintescalationhistory",
        "app_complaintticketextradetail",
    ],
    "reopen-history": ["app_complaintreopenhistory"],
    "address-change": ["app_complaintaddresschangerequest"],
    "notifications": ["app_complaintnotification"],
    "feedback": ["app_complaintfeedback"],
    # Schedule Setup
    "staff-templates": ["app_stafftemplate"],
    "alternative-staff-templates": ["app_alternativestafftemplate"],
    "collection-points": ["app_collection_point"],
    "trip-plans": ["app_tripplan", "app_tripplancollectionpoint", "app_tripplanvehiclesubstitution"],
    # Daily Operations ("Daily Trip Plan" = the first three)
    "daily-trip-assignments": ["app_dailytripassignment"],
    # Static Route Map: detours drawn on the map, each plan's saved route and
    # each daily trip's copy of it (app/services/static_route.py).
    "static-route-map": ["app_routedetourwaypoint", "app_tripplanstaticroute", "app_dailytripstaticroute"],
    "daily-trip-collection-points": ["app_dailytripcollectionpoint"],
    "daily-trip-household-collections": ["app_dailytriphouseholdcollection"],
    "bin-collection-events": ["app_bincollectionevent"],
    "daily-trip-logs": ["app_dailytriplog"],
    "wastecollections": ["app_wastecollection"],
    "vehicle-breakdowns": ["app_vehiclebreakdown", "app_vehiclebreakdownphoto"],
    "trip-delay-reports": ["app_tripdelayreport"],
    "retrip-requests": ["app_tripretriprequest"],
    "staff-notifications": ["app_staffnotification", "app_dailyservicenotice"],
    # Attendance
    "attendance": ["app_employee", "app_recognized", "attendancenew"],
    # Reports
    "daily-waste-comparisons": ["daily_waste_comparison"],
    "monthly-waste-comparison": ["monthly_weight_report"],
    # Fleet & Reports
    "weighbridge-management": ["app_weighbridgecheck"],
}

# sub-module -> sub-modules whose rows it can't work without.
SCREEN_REQUIRES = {
    "complaint-audit": ["tickets"],
    "countries": ["continents"],
    "states": ["countries"],
    "designation-masters": ["department-masters"],
    "subproperties": ["properties"],
    "customer-access-configuration": ["customercreations"],
    "panchayat-leaders": ["panchayat"],
    "district-leaders": ["districts"],
    "subcategories": ["categories"],
    "sla-rules": ["categories"],
    "reopen-history": ["tickets"],
    "address-change": ["tickets"],
    "notifications": ["tickets"],
    "feedback": ["tickets"],
    "alternative-staff-templates": ["staff-templates"],
    "trip-plans": ["staff-templates", "vehicle-creation"],
    "daily-trip-assignments": ["trip-plans"],
    "static-route-map": ["trip-plans", "daily-trip-assignments"],
    "daily-trip-collection-points": ["daily-trip-assignments"],
    "daily-trip-household-collections": ["daily-trip-assignments"],
    "bin-collection-events": ["daily-trip-assignments"],
    "daily-trip-logs": ["daily-trip-assignments"],
    "wastecollections": ["daily-trip-assignments"],
    "vehicle-breakdowns": ["daily-trip-assignments"],
    "trip-delay-reports": ["daily-trip-assignments"],
    "retrip-requests": ["daily-trip-assignments"],
    "weighbridge-management": ["daily-trip-assignments"],
}

# Columns every IWMS query relies on: never dropped.
KEEP_COLUMNS = frozenset({
    "id", "unique_id", "company_id", "project_id", "is_active", "is_deleted", "delete_reason",
    "created_by_id", "updated_by_id", "created_at", "updated_at", "deleted_at", "deleted_by_id",
})


def _screen_index():
    """{screen name: (module code, catalog screen)} for company screens."""
    return {s["name"]: (code, s) for code, m in catalog.MODULES.items() for s in m["screens"]
            if not s.get("superadmin_only")}


SCREEN_MODULE = {name: code for name, (code, _s) in _screen_index().items()}
MODULE_TABLES = {}
for _name, _tables in SCREEN_TABLES.items():
    MODULE_TABLES.setdefault(SCREEN_MODULE[_name], []).extend(t for t in _tables if t not in SYSTEM_TABLES)


# ------------------------------------------------------------------ the graph

def _closure(start, step):
    out, todo = set(), list(start)
    while todo:
        c = todo.pop()
        if c not in out:
            out.add(c)
            todo.extend(step(c))
    return out


def droppable_tables(screen):
    """The sub-module's tables that can be dropped (company tables, not system)."""
    return [t for t in SCREEN_TABLES.get(screen, []) if _model(t) and t not in SYSTEM_TABLES]


def screen_requires(name):
    """Every sub-module `name` needs, transitively (not itself)."""
    return _closure(SCREEN_REQUIRES.get(name, []), lambda s: SCREEN_REQUIRES.get(s, [])) - {name}


def screen_dependents(name):
    return {s for s in SCREEN_TABLES if name in screen_requires(s)}


def module_screens(code):
    """The module's sub-modules (every company screen: each can be turned off
    in a project; only those with droppable tables change the database)."""
    return [s["name"] for s in catalog.MODULES.get(code, {}).get("screens", []) if not s.get("superadmin_only")]


def _module_deps(code):
    return sorted({SCREEN_MODULE[r] for s in module_screens(code) for r in screen_requires(s)} - {code})


DEPENDS_ON = {}   # filled in at the end of this module (needs _model)


def requires(code):
    return _closure(DEPENDS_ON.get(code, []), lambda c: DEPENDS_ON.get(c, [])) - {code}


def dependents(code):
    return {m for m in DEPENDS_ON if code in requires(m)}


def cascade_screens(before, wanted):
    """Sub-modules on after a change from `before` to `wanted`: turning one
    off turns off what needs it (unless turned on in the same change), then
    everything left brings in what it needs."""
    before, wanted = set(before), set(wanted)
    added = wanted - before
    final = set(wanted)
    for name in before - wanted:
        final -= screen_dependents(name) - added
    for name in list(final):
        final |= screen_requires(name)
    return final


def cascade(before, wanted):
    """Modules on after a change (all sub-modules of each): see cascade_screens."""
    def screens(codes):
        return {s for c in codes for s in module_screens(c)}
    before, wanted = set(before), set(wanted)
    final_screens = cascade_screens(screens(before), screens(wanted))
    final = {c for c in wanted if not module_screens(c) or set(module_screens(c)) & final_screens}
    final |= {SCREEN_MODULE[s] for s in final_screens}
    return final


def graph_json():
    """For the console: {code: {core, tables, depends_on}}."""
    return {code: {"core": code in CORE_MODULES,
                   "tables": [t for t in MODULE_TABLES.get(code, []) if _model(t)],
                   "depends_on": DEPENDS_ON.get(code, [])}
            for code in catalog.MODULES}


# ------------------------------------------------------------------ models

_BY_TABLE = None


def _model(table):
    """The live model class of a company-DB table (None for master tables)."""
    global _BY_TABLE
    if _BY_TABLE is None:
        _BY_TABLE = {m._meta.db_table: m for m in apps.get_app_config("app").get_models()
                     if not m._meta.proxy and m._meta.managed and is_tenant_model(m)}
    return _BY_TABLE.get(table)


def table_module(table):
    for code, tables in MODULE_TABLES.items():
        if table in tables:
            return code
    return None


def _state_apps(alias):
    """Model classes as the company DB's applied migrations describe them."""
    from django.db.migrations.executor import MigrationExecutor

    try:
        executor = MigrationExecutor(connections[alias])
        if "app" in executor.loader.unmigrated_apps:
            return None  # migrations disabled (tests): the code is the schema
        return executor._create_project_state(with_applied_migrations=True).apps
    except Exception:
        logger.exception("Could not load the migration state of %s", alias)
        return None


def _historical(state_apps, model):
    """The model as the applied migrations describe it; None when those
    migrations haven't created it yet (migrate will)."""
    if state_apps is not None:
        try:
            return state_apps.get_model(model._meta.app_label, model._meta.model_name)
        except LookupError:
            if state_apps.all_models.get(model._meta.app_label):
                return None
    return model  # migrations disabled (tests): the code is the schema


def _fk_order(models):
    """Parents before children (real FKs inside the set)."""
    tables = {m._meta.db_table for m in models}
    ordered, left = [], list(models)
    while left:
        for m in left:
            parents = {f.related_model._meta.db_table for f in m._meta.concrete_fields
                       if f.is_relation and f.related_model is not None} & tables
            if parents <= {x._meta.db_table for x in ordered} | {m._meta.db_table}:
                ordered.append(m)
                left.remove(m)
                break
        else:
            ordered.extend(left)
            break
    return ordered


def existing_tables(alias):
    return set(connections[alias].introspection.table_names())


# ------------------------------------------------------------------ audit triggers

def _rebuild_triggers(alias, table):
    """DB Change Audit triggers name every column: rebuild them for `table`."""
    from app.services import db_change_audit as audit

    conn = connections[alias]
    if not audit.is_enabled(conn) or audit.AUDIT_TABLE not in existing_tables(alias):
        return  # DB Change Audit turned off for this company: no triggers
    try:
        with conn.cursor() as cursor:
            for _op, suffix in audit._OPERATIONS:
                cursor.execute(f"DROP TRIGGER IF EXISTS {audit._quote(audit._trigger_name(table, suffix))}")
            tables = audit._audited_tables(cursor)
            if table in tables:
                for statement in audit.build_trigger_sql(table, tables[table]):
                    cursor.execute(statement)
    except Exception:
        logger.exception("Could not rebuild DB change audit triggers for %s", table)


def _drop_triggers(alias, table):
    from app.services import db_change_audit as audit

    conn = connections[alias]
    if conn.vendor != "mysql":
        return
    with conn.cursor() as cursor:
        for _op, suffix in audit._OPERATIONS:
            cursor.execute(f"DROP TRIGGER IF EXISTS {audit._quote(audit._trigger_name(table, suffix))}")


# ------------------------------------------------------------------ fields

def droppable_field(model, field):
    return (field.concrete and not field.primary_key and not field.unique
            and field.column not in KEEP_COLUMNS and field.name not in KEEP_COLUMNS)


def form_model(screen):
    """The model of the sub-module's form (its first table), or None."""
    tables = SCREEN_TABLES.get(screen) or []
    if not tables or tables[0] in SYSTEM_TABLES:
        return None  # sign-in tables keep every column
    return _model(tables[0])


def screen_fields(screen):
    """The form's fields that can be turned off (columns that can go)."""
    from .iwms_forms import _form_fields

    model = form_model(screen)
    if model is None:
        return []
    return [f for f in _form_fields(model) if droppable_field(model, f)]


def _field_label(f):
    from .iwms_forms import friendly_label

    explicit = str(f.verbose_name) if f.verbose_name and f.verbose_name != f.name.replace("_", " ") else ""
    return explicit[:1].upper() + explicit[1:] if explicit else friendly_label(f.name)


# ------------------------------------------------------------------ project state

def project_states(alias):
    """{project pk: {"modules": {codes}, "screens": {name: {"enabled", "off_fields"}}}}
    for the company's live projects."""
    from app.models.superadmin_masters.project import Project
    from app.models.tenancy.tenant import ProjectModule, ProjectScreen

    states = {pk: {"modules": set(), "screens": {}}
              for pk in Project.objects.using(alias).filter(is_deleted=False).values_list("pk", flat=True)}
    for pid, code in ProjectModule.objects.using(alias).filter(project_id__in=states).values_list(
            "project_id", "module_code"):
        states[pid]["modules"].add(code)
    if ProjectScreen._meta.db_table in existing_tables(alias):
        for row in ProjectScreen.objects.using(alias).filter(project_id__in=states):
            states[row.project_id]["screens"][row.screen] = {
                "enabled": row.is_enabled, "off_fields": list(row.off_fields or [])}
    return states


def enabled_screens(state):
    """Sub-modules on in a project: those of its modules not turned off."""
    return {s for code in state["modules"] if code not in HIDDEN_MODULES for s in module_screens(code)
            if state["screens"].get(s, {}).get("enabled", True)}


def plan(states):
    """What the company DB should hold: {"tables": {..}, "off_columns": {table: {columns}}}."""
    on_by_project = {pid: enabled_screens(st) for pid, st in states.items()}
    used = set().union(*on_by_project.values()) if on_by_project else set()
    for s in list(used):
        used |= screen_requires(s)
    tables = {t for s in used for t in droppable_tables(s)}

    off_columns = {}
    for s in used:
        model = form_model(s)
        if model is None:
            continue
        users = [pid for pid, on in on_by_project.items() if s in on]
        if not users:
            continue  # only kept because another sub-module needs it: keep every column
        names = {f.name: f for f in screen_fields(s)}
        off = set(names)
        for pid in users:
            off &= set(states[pid]["screens"].get(s, {}).get("off_fields", []))
        if off:
            off_columns.setdefault(model._meta.db_table, set()).update(names[n].column for n in off)
    return {"tables": tables, "off_columns": off_columns}


def _dropped(alias):
    from app.models.tenancy.tenant import DroppedColumn

    if DroppedColumn._meta.db_table not in existing_tables(alias):
        return set()
    return set(DroppedColumn.objects.using(alias).values_list("table_name", "column_name"))


def diff(alias, target, counts=False):
    """The changes that turn the company DB into `target` (see plan())."""
    have = existing_tables(alias)
    dropped = _dropped(alias)
    all_tables = {t for s in SCREEN_TABLES for t in droppable_tables(s)}
    out = {
        "create_tables": sorted(t for t in target["tables"] if t not in have),
        "drop_tables": sorted(t for t in all_tables - target["tables"] if t in have),
        "drop_columns": [], "add_columns": [],
    }
    for table in sorted(target["tables"]):
        model = _model(table)
        off = target["off_columns"].get(table, set())
        for f in model._meta.concrete_fields:
            if not droppable_field(model, f):
                continue
            gone = (table, f.column) in dropped
            if f.column in off and not gone and table in have:
                out["drop_columns"].append(f"{table}.{f.column}")
            elif f.column not in off and gone:
                out["add_columns"].append(f"{table}.{f.column}")
    if counts:
        out["rows"] = {t: _model(t)._base_manager.using(alias).count() for t in out["drop_tables"]}
        values = {}
        for tc in out["drop_columns"]:
            table, column = tc.split(".", 1)
            model = _model(table)
            field = next(f for f in model._meta.concrete_fields if f.column == column)
            values[tc] = model._base_manager.using(alias).exclude(**{f"{field.attname}__isnull": True}).count()
        out["values"] = values
    return out


def apply(alias, changes):
    """Carry out a diff(): drop/create tables, drop/add columns."""
    if not any(changes[k] for k in ("create_tables", "drop_tables", "drop_columns", "add_columns")):
        return changes
    conn = connections[alias]
    state_apps = _state_apps(alias)

    drop = [_model(t) for t in changes["drop_tables"]]
    for model in reversed(_fk_order(drop)):
        table = model._meta.db_table
        _drop_triggers(alias, table)
        with conn.schema_editor() as editor:
            editor.delete_model(_historical(state_apps, model) or model)
    if drop:
        from app.models.tenancy.tenant import DroppedColumn

        DroppedColumn.objects.using(alias).filter(table_name__in=changes["drop_tables"]).delete()

    created = []
    for model in _fk_order([_model(t) for t in changes["create_tables"]]):
        hist = _historical(state_apps, model)
        if hist is None:
            continue  # a pending migration creates it
        with conn.schema_editor() as editor:
            editor.create_model(hist)
        created.append(model._meta.db_table)

    if created:
        _reset_cache(alias)   # the ORM must see the new tables before they're filled
        # A recreated lookup starts from the master DB's template rows.
        from .shared_rows import copy_shared_rows

        copy_shared_rows(alias, only=created)
        _backfill_created(alias, created)
    touched = set(created)
    for tc in changes["drop_columns"]:
        table, column = tc.split(".", 1)
        model = _model(table)
        field = next(f for f in model._meta.concrete_fields if f.column == column)
        _drop_column(alias, conn, state_apps, model, field)
        touched.add(table)
    for tc in changes["add_columns"]:
        table, column = tc.split(".", 1)
        model = _model(table)
        field = next(f for f in model._meta.concrete_fields if f.column == column)
        _add_column(alias, conn, state_apps, model, field)
        touched.add(table)
    from app.services import db_change_audit as audit

    if audit.AUDIT_TABLE in changes["drop_tables"]:
        audit.drop_triggers(alias)       # they all write to the table just dropped
    elif audit.AUDIT_TABLE in created:
        try:
            audit.install_triggers(alias)
        except Exception:
            logger.exception("Could not install DB change audit triggers on %s", alias)
    else:
        for table in touched:
            _rebuild_triggers(alias, table)
    _reset_cache(alias)
    logger.info("%s: %s", alias, {k: v for k, v in changes.items() if v})
    return changes


def sync_company(company, alias=None):
    """Make the company DB match its projects. Returns what changed."""
    from .db import register_tenant_db

    alias = alias or register_tenant_db(company.db_name)
    return apply(alias, diff(alias, plan(project_states(alias))))


def preview(company, project_id, modules, screens):
    """What saving a project with these modules/sub-modules/fields would do to
    the company DB (nothing is changed). project_id None = a new project."""
    from .db import register_tenant_db

    alias = register_tenant_db(company.db_name)
    states = project_states(alias)
    final = resolve(states.get(project_id) if project_id else None, modules, screens)
    states[project_id or "__new__"] = final
    out = diff(alias, plan(states), counts=True)
    out["modules"] = sorted(final["modules"])
    out["screens"] = {s: c["enabled"] for s, c in final["screens"].items()}
    return out


def restore_all(alias):
    """Before migrate: every table and column back, so migrations apply."""
    every = {"tables": {t for s in SCREEN_TABLES for t in droppable_tables(s)}, "off_columns": {}}
    apply(alias, diff(alias, every))
    ensure_tables(alias)


def _backfill_created(alias, created):
    """A new Complaint Audit table starts with the tickets' history so far."""
    if "complaint_audit_logs" in created:
        from app.services.complaint_audit_log import backfill

        try:
            backfill(alias)
        except Exception:
            logger.exception("Could not backfill the complaint audit log on %s", alias)


def ensure_tables(alias):
    """Create company tables the DB lacks although its migrations are applied
    (models that moved from the master DB into every company DB), with the
    master DB's template rows. Returns the tables created."""
    have = existing_tables(alias)
    state_apps = _state_apps(alias)
    conn = connections[alias]
    created = []
    models = [m for m in apps.get_app_config("app").get_models()
              if not m._meta.proxy and m._meta.managed and is_tenant_model(m) and m._meta.db_table not in have]
    for model in _fk_order(models):
        hist = _historical(state_apps, model)
        if hist is None:
            continue
        with conn.schema_editor() as editor:
            editor.create_model(hist)
        created.append(model._meta.db_table)
    if created:
        _reset_cache(alias)
        from .shared_rows import copy_shared_rows

        copy_shared_rows(alias, only=created)
        _backfill_created(alias, created)
        for table in created:
            _rebuild_triggers(alias, table)
        _reset_cache(alias)
        logger.info("%s: created company tables %s", alias, created)
    return created


def table_rows(alias):
    """{module code: {"rows", "tables"}} of its tables in the company DB."""
    have = existing_tables(alias)
    out = {}
    for code, tables in MODULE_TABLES.items():
        present = [t for t in tables if _model(t) and t in have]
        out[code] = {"rows": sum(_model(t)._base_manager.using(alias).count() for t in present),
                     "tables": present}
    return out


# ------------------------------------------------------------------ the tree (console)

def structure(company, project_id=None):
    """Module → sub-module (grouped) → form → fields, with what's on in the
    project (a new project: everything on once its module is ticked).
    company None: the plain tree (new company wizard)."""
    from .db import register_tenant_db

    alias = register_tenant_db(company.db_name) if company is not None else None
    state = project_states(alias).get(project_id) if alias and project_id else None
    modules_on = state["modules"] if state else set()
    screens_state = state["screens"] if state else {}
    have = existing_tables(alias) if alias else set()
    out = []
    for code, module in catalog.MODULES.items():
        if code in HIDDEN_MODULES:
            continue
        groups, order = {}, []
        for s in module["screens"]:
            if s.get("superadmin_only"):
                continue
            key = s.get("group") or s["name"]
            if key not in groups:
                groups[key] = {"key": key, "label": catalog.GROUP_LABELS.get(key, s["label"]) if s.get("group")
                               else s["label"], "forms": []}
                order.append(key)
            st = screens_state.get(s["name"], {})
            fields = screen_fields(s["name"])
            off = set(st.get("off_fields", []))
            model = _screen_model_safe(s["name"])
            groups[key]["forms"].append({
                "name": s["name"], "label": s["label"],
                "tables": droppable_tables(s["name"]),
                "kept_tables": [t for t in SCREEN_TABLES.get(s["name"], []) if t in SYSTEM_TABLES],
                "shared": bool(model is not None and not is_tenant_model(model)),
                "in_db": any(t in have for t in SCREEN_TABLES.get(s["name"], [])),
                "requires": sorted(screen_requires(s["name"])),
                "enabled": code in modules_on and st.get("enabled", True),
                "fields": [{"name": f.name, "label": _field_label(f), "enabled": f.name not in off}
                           for f in fields],
            })
        out.append({"code": code, "name": module.get("label") or code, "core": code in CORE_MODULES,
                    "depends_on": DEPENDS_ON.get(code, []), "enabled": code in modules_on,
                    "sub_modules": [groups[k] for k in order]})
    return out


def _screen_model_safe(name):
    from .iwms_forms import _screen_model

    try:
        return _screen_model(name)
    except Exception:
        return None


def clean_screens(data):
    """{screen: {"enabled": bool, "off_fields": [names]}} from the console."""
    from rest_framework.exceptions import ValidationError

    if data in (None, ""):
        return None
    if not isinstance(data, dict):
        raise ValidationError({"screens": "Send {sub-module: {enabled, off_fields}}."})
    index = _screen_index()
    out = {}
    for name, cfg in data.items():
        if name not in index or not isinstance(cfg, dict):
            raise ValidationError({"screens": f"Unknown sub-module {name!r}."})
        allowed = {f.name for f in screen_fields(name)}
        off = [f for f in (cfg.get("off_fields") or []) if isinstance(f, str)]
        unknown = set(off) - allowed
        if unknown:
            raise ValidationError({"screens": f"{index[name][1]['label']}: unknown field(s) {', '.join(sorted(unknown))}."})
        out[name] = {"enabled": bool(cfg.get("enabled", True)), "off_fields": sorted(set(off))}
    return out


def resolve(before_state, modules, screens):
    """A project's modules and sub-modules after the cascade:
    returns {"modules": {codes}, "screens": {name: {enabled, off_fields}}}."""
    screens = screens or {}
    modules = set(modules) - HIDDEN_MODULES
    before_on = enabled_screens(before_state) if before_state else set()
    wanted = {"modules": set(modules), "screens": {**(before_state or {}).get("screens", {}), **screens}}
    final_on = cascade_screens(before_on, enabled_screens(wanted))
    final_modules = {c for c in modules if not module_screens(c) or set(module_screens(c)) & final_on}
    final_modules |= {SCREEN_MODULE[s] for s in final_on}
    return {"modules": final_modules, "screens": {
        s: {"enabled": s in final_on, "off_fields": wanted["screens"].get(s, {}).get("off_fields", [])}
        for code in final_modules for s in module_screens(code)}}


def save_project_screens(alias, project, before_state, modules, screens):
    """Store the project's sub-modules/fields after the cascade. Returns the
    final module codes (the cascade may add or remove modules)."""
    from app.models.tenancy.tenant import ProjectScreen

    final = resolve(before_state, modules, screens)
    for s, cfg in final["screens"].items():
        ProjectScreen.objects.using(alias).update_or_create(
            project=project, screen=s, defaults={"is_enabled": cfg["enabled"], "off_fields": cfg["off_fields"]})
    return final["modules"]


# ------------------------------------------------------------------ columns

def _hist_field(state_apps, model, field):
    hist = _historical(state_apps, model) or model
    try:
        return hist, hist._meta.get_field(field.name)
    except Exception:
        return model, field


def _drop_column(alias, conn, state_apps, model, field):
    from app.models.tenancy.tenant import DroppedColumn

    table = model._meta.db_table
    _drop_triggers(alias, table)
    hist, hfield = _hist_field(state_apps, model, field)
    with conn.schema_editor() as editor:
        editor.remove_field(hist, hfield)
    DroppedColumn.objects.using(alias).get_or_create(table_name=table, column_name=field.column)


def _add_column(alias, conn, state_apps, model, field):
    from app.models.tenancy.tenant import DroppedColumn

    table = model._meta.db_table
    _drop_triggers(alias, table)
    hist, hfield = _hist_field(state_apps, model, field)
    try:
        with transaction.atomic(using=alias), conn.schema_editor() as editor:
            editor.add_field(hist, hfield)
    except Exception:
        # Existing rows have no value for a NOT NULL column without a default:
        # add it nullable (the app fills it on the next save).
        loose = hfield.clone()
        loose.null = True
        loose.set_attributes_from_name(hfield.name)
        loose.model = hist
        with conn.schema_editor() as editor:
            editor.add_field(hist, loose)
    DroppedColumn.objects.using(alias).filter(table_name=table, column_name=field.column).delete()


# ------------------------------------------------------------------ ORM cache

_CACHE = {}       # alias -> (expires, frozenset of (table, column))
_TTL = 5.0


def dropped_columns(alias):
    """(table, column) pairs missing from this company DB; cached briefly.
    Read with a raw cursor: the ORM asks this while compiling queries."""
    hit = _CACHE.get(alias)
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1]
    value = frozenset()
    try:
        with connections[alias].cursor() as cursor:
            cursor.execute("SELECT table_name, column_name FROM tenancy_dropped_column")
            value = frozenset((t, c) for t, c in cursor.fetchall())
    except Exception:
        value = frozenset()  # table not migrated yet
    _CACHE[alias] = (now + _TTL, value)
    return value


_TABLE_CACHE = {}  # alias -> (expires, frozenset of module tables missing)


def missing_tables(alias):
    """Module tables dropped from this company DB; cached briefly."""
    hit = _TABLE_CACHE.get(alias)
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1]
    try:
        have = existing_tables(alias)
        value = frozenset(t for names in MODULE_TABLES.values() for t in names if _model(t) and t not in have)
    except Exception:
        value = frozenset()
    _TABLE_CACHE[alias] = (now + _TTL, value)
    return value


def _reset_cache(alias):
    _CACHE.pop(alias, None)
    _TABLE_CACHE.pop(alias, None)


DEPENDS_ON.update({code: deps for code in catalog.MODULES if (deps := _module_deps(code))})

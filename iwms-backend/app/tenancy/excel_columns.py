"""Excel columns per company / project: which fields each screen exports to
Excel and which fields its upload template (and the upload itself) has.

* screens_tree(): every IWMS module -> sub-module (catalog screen) with the
  fields it can export (the list API's fields) and import (the fields the
  create API accepts; `required` ones are always in the template).
* ExcelColumnSetting rows (master DB) hold the super admin's choice:
  [{field, header}] in order, per company (project_id "") or per project and
  per kind ("export" / "import").
* resolve(): project row -> company row -> None (= every field, as before).
* Enforced on the server: the Excel export writes only the enabled columns
  (viewsets/core_modules/excel_export_viewset.py) and ImportColumnsMiddleware
  removes disabled columns from bulk uploads. The list pages read the same
  setting (GET ws/excel-columns/) for the template and the page export.
"""
import csv
import functools
import io
import logging

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import Resolver404, resolve as resolve_url
from rest_framework.exceptions import ValidationError

from app.utils import permission_catalog as catalog

from .iwms_forms import friendly_label

logger = logging.getLogger(__name__)

EXPORT, IMPORT = "export", "import"
KINDS = (EXPORT, IMPORT)
HEADER_LIMIT = 100

# Never offered: secrets and soft-delete bookkeeping.
NEVER_FIELDS = {
    "password", "previous_password", "fcm_token", "is_deleted", "delete_reason",
    "deleted_at", "deleted_by", "deleted_by_id",
}
# Stamped by the server on create, never filled from a sheet (same list as
# the upload template in SafeDataTable.tsx, METADATA_EXCLUDED_FIELDS).
# Tenancy keys (the list pages fill them) are not offered either; an upload
# keeps them whatever the import setting says.
TENANCY_KEYS = {"company_id", "project_id", "company_id_input", "project_id_input"}
NOT_IMPORTED = NEVER_FIELDS | TENANCY_KEYS | {
    "id", "unique_id", "created_at", "updated_at", "created_by", "updated_by",
    "created_by_id", "updated_by_id", "is_active",
}


def field_variants(name):
    """Column names that mean the field `name` in an upload sheet: lookup
    fields are also filled by name (ward_id -> ward_name / ward,
    waste_type_ids -> waste_type_names)."""
    out = [name]
    if name.endswith("_ids"):
        out += [name[:-4] + "_names", name[:-4]]
    elif name.endswith("_id"):
        out += [name[:-3] + "_name", name[:-3]]
    return out


# ---------------------------------------------------------------- catalog

def _serializer_fields(module, screen, action):
    """The serializer fields of the screen's first API route, or None."""
    from rest_framework.request import Request
    from rest_framework.test import APIRequestFactory

    for route in catalog.screen_routes(module, screen):
        path = f"/api/v1/{route}/"
        try:
            match = resolve_url(path)
        except Resolver404:
            continue
        cls = getattr(match.func, "cls", None)
        if cls is None or not hasattr(cls, "get_serializer_class"):
            continue
        try:
            view = cls()
            view.action, view.format_kwarg, view.kwargs, view.args = action, None, {}, ()
            view.request = Request(APIRequestFactory().get(path))
            serializer = view.get_serializer_class()(context={"request": view.request, "view": view})
            return dict(serializer.fields)
        except Exception:  # a view that needs a real request: try the next route
            logger.debug("No serializer fields for %s (%s)", path, action, exc_info=True)
    return None


LABELS = {"id": "ID", "unique_id": "Unique ID"}
# Bookkeeping columns a list API returns but no form has.
SYSTEM_FIELDS = {"id", "unique_id", "created_at", "updated_at", "created_by", "updated_by",
                 "created_by_id", "updated_by_id"}


def _field(name, required=False, label=None, source=None):
    out = {"name": name, "label": label or LABELS.get(name) or friendly_label(name), "required": bool(required)}
    if source is not None:
        out["source"] = source
    return out


@functools.lru_cache(maxsize=1)
def _api_fields():
    """{screen: {"read": {name: required} | None, "write": {...} | None}} from
    each screen's list/create serializer (slow: built once per process)."""
    out = {}
    for module in catalog.MODULES.values():
        for screen in module["screens"]:
            if screen["superadmin_only"] or screen["name"] in out:
                continue
            read = _serializer_fields(module, screen, "list")
            write = _serializer_fields(module, screen, "create")
            out[screen["name"]] = {
                "read": None if read is None else {n: False for n, f in read.items() if not f.write_only},
                "write": None if write is None else {n: f.required for n, f in write.items() if not f.read_only},
            }
    return out


def _export_source(name, read):
    """The list API key that shows a form field: continent_id -> continent_name."""
    if read is None:
        return name
    for key in field_variants(name)[1:2] + [name]:
        if key in read:
            return key
    return None


def _screen_fields(screen, form):
    """{"export": [...], "import": [...]}: the form's fields, in the form's
    order and with its labels (as in Permissions & fields). A screen without
    a form falls back to its API fields (bookkeeping columns left out)."""
    api = _api_fields().get(screen["name"], {})
    read, write = api.get("read"), api.get("write")
    form_fields = [f for f in (form or {}).get("fields", []) if not f.get("hidden")]
    if not form_fields and write:
        # No form definition: the fields the create form submits.
        form_fields = [{"name": n, "label": None, "required": req} for n, req in write.items()
                       if n not in NEVER_FIELDS | SYSTEM_FIELDS and not n.startswith("_")]
    if form_fields:
        names = {f["name"] for f in form_fields}
        export, imp = [], []
        for f in form_fields:
            name = f["name"]
            if name in NEVER_FIELDS:
                continue
            source = _export_source(name, read)
            # two form fields showing the same list column: keep the one named like it
            if source is not None and not (source != name and source in names):
                export.append(_field(name, label=f["label"], source=source))
            if name not in NOT_IMPORTED and (write is None or name in write):
                imp.append(_field(name, bool(f.get("required")) or bool((write or {}).get(name)), f["label"]))
        for fields in (export, imp):   # one label per Excel column
            labels = [f["label"].lower() for f in fields]
            for f in fields:
                if labels.count(f["label"].lower()) > 1:
                    f["label"] = "Active" if f["name"] == "is_active" else f'{f["label"]} ({f["name"]})'
        return {EXPORT: export, IMPORT: imp}
    if read is None and write is None:
        return {EXPORT: [], IMPORT: []}
    read = read if read is not None else write
    write = write if write is not None else {}
    return {
        EXPORT: [_field(n, source=n) for n in read
                 if n not in NEVER_FIELDS | SYSTEM_FIELDS and not n.startswith("_")],
        IMPORT: [_field(n, req) for n, req in write.items()
                 if n not in NOT_IMPORTED and not n.startswith("_")],
    }


_TREE_TTL = 30.0
_tree_cache = {}


def _module_tree(module):
    """The module's Excel screens; cached briefly (form labels/hidden fields
    are edited in the console, so not forever)."""
    import time

    hit = _tree_cache.get(module["name"])
    if hit and hit[0] > time.monotonic():
        return hit[1]
    screens = _build_module_tree(module)
    _tree_cache[module["name"]] = (time.monotonic() + _TREE_TTL, screens)
    return screens


def _build_module_tree(module):
    from .iwms_forms import configured_forms

    try:
        forms = {f["name"]: f for f in configured_forms(module["name"])}
    except Exception:
        logger.exception("Could not read the forms of %s", module["name"])
        forms = {}
    screens = []
    for screen in module["screens"]:
        if screen["superadmin_only"]:
            continue
        fields = _screen_fields(screen, forms.get(screen["name"]))
        if not fields[EXPORT] and not fields[IMPORT]:
            continue  # dashboards, maps, tracking: no Excel table
        screens.append({
            "name": screen["name"], "label": screen["label"],
            "group": catalog.GROUP_LABELS.get(screen["group"]) if screen["group"] else None,
            EXPORT: fields[EXPORT], IMPORT: fields[IMPORT],
        })
    return screens


def screens_tree():
    """[{name, label, screens: [{name, label, group, export: [...], import: [...]}]}]"""
    modules, seen = [], set()
    for module in catalog.MODULES.values():
        screens = [s for s in _module_tree(module) if s["name"] not in seen]
        seen |= {s["name"] for s in screens}
        if screens:
            modules.append({"name": module["name"], "label": module["label"], "screens": screens})
    return modules


def screen_fields(screen_name):
    for module in catalog.MODULES.values():
        if any(s["name"] == screen_name for s in module["screens"]):
            for screen in _module_tree(module):
                if screen["name"] == screen_name:
                    return screen
    return None


def match_field(fields, name):
    """The catalog field a stored / sent column means: its own name, a lookup
    variant (continent_name -> continent_id) or its export source."""
    for f in fields:
        if name == f["name"] or name == f.get("source"):
            return f
    for f in fields:
        if name in field_variants(f["name"]) or f["name"] in field_variants(name):
            return f
    return None


def normalize_columns(screen_name, kind, columns):
    """Saved columns in today's catalog names (older settings may name API
    fields); columns no longer on the form are dropped."""
    fields = (screen_fields(screen_name) or {}).get(kind, [])
    out, seen = [], set()
    for c in columns or []:
        f = match_field(fields, c.get("field"))
        if f and f["name"] not in seen:
            seen.add(f["name"])
            out.append({"field": f["name"], "header": c.get("header") or f["label"]})
    return out


def check_screen(screen_name):
    fields = screen_fields(str(screen_name or ""))
    if fields is None:
        raise ValidationError({"screen": f"Unknown screen: {screen_name!r}."})
    return fields


# ---------------------------------------------------------------- availability

PERMISSION_REASON = "Hidden in Permissions & fields"
DATABASE_REASON = "Removed from the database (Edit project → Modules)"
SCREEN_OFF_REASON = "This sub-module is turned off (Edit project → Modules)"
NOT_ON_FORM_REASON = "Not a field of this form"


def on_form(field, names):
    """continent_name stands for the form's continent_id; created_at is no form field."""
    return names is None or bool(_field_keys(field) & names)


def _field_keys(name):
    """Model field names a catalog field stands for: ward_name / ward -> ward_id."""
    keys = {name, name + "_id", name + "_ids"}
    if name.endswith("_names"):
        keys.add(name[:-6] + "_ids")
    elif name.endswith("_name"):
        keys.add(name[:-5] + "_id")
    return keys


def _denied(company, project_ids):
    """({project: {screen: {model field: reason}}}, {project: {screens off}}) for
    the company's projects: fields hidden in Permissions & fields
    (CompanyUserScreenColumnPermission can_view off) or dropped from the
    project's database, and sub-modules turned off there."""
    from app.models.superadmin.screen_management.companyuserscreencolumnpermission import (
        CompanyUserScreenColumnPermission,
    )
    from app.models.superadmin.screen_management.userscreen import UserScreen
    from app.models.superadmin.screen_management.userscreencolumn import UserScreenColumn

    from .db import register_tenant_db
    from .iwms_tables import HIDDEN_MODULES, module_screens, project_states

    fields = {pid: {} for pid in project_ids}
    screens_off = {pid: set() for pid in project_ids}
    if company is None or not company.db_name or not project_ids:
        return fields, screens_off
    alias = register_tenant_db(company.db_name)

    states = project_states(alias)
    for pid in project_ids:
        state = states.get(pid)
        if state is None:
            continue
        for code in catalog.MODULES:
            if code in HIDDEN_MODULES:
                continue
            for screen in module_screens(code):
                info = state["screens"].get(screen, {})
                if code not in state["modules"] or not info.get("enabled", True):
                    screens_off[pid].add(screen)
                for name in info.get("off_fields") or ():
                    fields[pid].setdefault(screen, {})[name] = DATABASE_REASON

    perms = [p for p in CompanyUserScreenColumnPermission.objects.using(alias).filter(
        company_id=company.unique_id, project_id__in=project_ids, is_deleted=False).values_list(
        "project_id", "userscreen_id", "column_id", "can_view", "is_active") if not (p[3] and p[4])]
    if perms:
        screen_names = dict(UserScreen.objects.using(alias).filter(is_deleted=False)
                            .values_list("pk", "userscreen_name"))
        columns = dict(UserScreenColumn.objects.using(alias).filter(pk__in={p[2] for p in perms})
                       .values_list("pk", "field_name"))
        for pid, screen_id, column_id, _view, _active in perms:
            screen, name = screen_names.get(screen_id, screen_id), columns.get(column_id)
            if screen and name:
                fields[pid].setdefault(screen, {}).setdefault(name, PERMISSION_REASON)
    return fields, screens_off


def _blocked_in(denied, field):
    for key in _field_keys(field):
        if key in denied:
            return denied[key]
    return None


def availability(company, project_id, projects=None):
    """What the Excel columns console must show as unavailable:
    {screen: {"off": reason | None, "export": {field: reason}, "import": {...},
    "partial": {field: [project names]}}} (screens with nothing blocked left out).

    Blocked: fields that aren't on the screen's form (created_at, unique_id),
    and fields hidden in Permissions & fields / removed from the database -
    for a project its own, for the whole company (project_id "") in any
    project. ("partial" is kept for older clients and stays empty.)"""
    projects = projects or []
    ids = [str(p["id"]) for p in projects] if not project_id else [str(project_id)]
    project_names = {str(p["id"]): p["name"] for p in projects}
    fields, screens_off = _denied(company, ids)
    out = {}
    for module in screens_tree():
        forms = {}
        try:
            from .iwms_forms import configured_forms

            forms = {f["name"]: ({x["name"] for x in f["fields"] if not x.get("hidden")} or None)
                     for f in configured_forms(module["name"])}
        except Exception:
            logger.exception("Could not read the forms of %s", module["name"])
        for screen in module["screens"]:
            name = screen["name"]
            names = forms.get(name)
            off = [pid for pid in ids if name in screens_off[pid]]
            entry = {"off": SCREEN_OFF_REASON if ids and len(off) == len(ids) else None,
                     EXPORT: {}, IMPORT: {}, "partial": {}}
            for kind in KINDS:
                for f in screen[kind]:
                    if not on_form(f["name"], names):
                        entry[kind][f["name"]] = NOT_ON_FORM_REASON
                        continue
                    reasons = {pid: _blocked_in(fields[pid].get(name, {}), f["name"]) for pid in ids}
                    hit = [pid for pid, r in reasons.items() if r]
                    if hit and project_id:
                        entry[kind][f["name"]] = reasons[hit[0]]
                    elif hit:
                        # The company setting reaches every project, so a field
                        # one project may not have can't be in it; a project
                        # that allows it can enable it in its own setting.
                        entry[kind][f["name"]] = "{} for {}".format(
                            reasons[hit[0]], ", ".join(project_names.get(pid, pid) for pid in hit))
            if entry["off"] or entry[EXPORT] or entry[IMPORT] or entry["partial"]:
                out[name] = entry
    return out


def blocked_for(company, project_id, screen_name, kind):
    """Catalog fields of one screen the project may not have in Excel."""
    if company is None or not project_id or not screen_name:
        return set()
    fields, _ = _denied(company, [str(project_id)])
    denied = fields[str(project_id)].get(screen_name, {})
    if not denied:
        return set()
    return {f["name"] for f in (screen_fields(screen_name) or {}).get(kind, []) if _blocked_in(denied, f["name"])}


def _sees_everything(request):
    """The company's developer login sees every field (screen_grants.developer_grants)."""
    return bool(getattr(getattr(request, "user", None), "is_developer", False))


# ---------------------------------------------------------------- settings

def clean_columns(screen_name, kind, raw, blocked=None):
    """[{field, header}] from the console, checked against the screen's fields.
    Import settings always keep the required fields (added at the end).
    `blocked`: {field: reason} the company/project may not have (availability)."""
    blocked = blocked or {}
    if kind not in KINDS:
        raise ValidationError({"kind": "Use export or import."})
    fields = check_screen(screen_name)[kind]
    known = {f["name"]: f for f in fields}
    if not isinstance(raw, list):
        raise ValidationError({"columns": "Must be a list."})
    columns, seen = [], set()
    for item in raw:
        match = match_field(fields, item.get("field")) if isinstance(item, dict) else None
        if match is None:
            raise ValidationError({"columns": f"Unknown field: {item!r}."})
        name = match["name"]
        if name in blocked:
            raise ValidationError({"columns": f'{known[name]["label"]} can\'t be used: {blocked[name]}.'})
        if name in seen:
            continue
        seen.add(name)
        header = item.get("header")
        if header is not None and not isinstance(header, str):
            raise ValidationError({"columns": "A header must be text."})
        columns.append({"field": name, "header": (header or "").strip()[:HEADER_LIMIT] or known[name]["label"]})
    if kind == IMPORT:
        columns += [{"field": f["name"], "header": f["label"]}
                    for f in fields if f["required"] and f["name"] not in seen and f["name"] not in blocked]
    if not columns:
        raise ValidationError({"columns": "Choose at least one field."})
    headers = [c["header"].lower() for c in columns]
    if len(set(headers)) != len(headers):
        raise ValidationError({"columns": "Two columns have the same header."})
    return columns


def resolve(company, project_id, screen_name, kind):
    """(columns, source): source = "project" | "company" | None (every field)."""
    from app.models.tenancy.master import ExcelColumnSetting

    if company is None or not screen_name:
        return None, None
    project_id = str(project_id or "")
    rows = {
        row.project_id: row
        for row in ExcelColumnSetting.objects.filter(
            company=company, screen=screen_name, kind=kind, project_id__in=[project_id, ""])
    }
    if project_id and project_id in rows:
        return rows[project_id].columns, "project"
    if "" in rows:
        return rows[""].columns, "company"
    return None, None


def settings_json(company, project_id):
    """{screen: {export: {columns, source}, import: {columns, source}}} for the
    screens with a setting that reaches this project (or the company)."""
    from app.models.tenancy.master import ExcelColumnSetting

    project_id = str(project_id or "")
    out = {}
    rows = ExcelColumnSetting.objects.filter(company=company, project_id__in=[project_id, ""])
    # company rows first, so project rows overwrite them
    for row in sorted(rows, key=lambda r: r.project_id != ""):
        out.setdefault(row.screen, {})[row.kind] = {
            "columns": normalize_columns(row.screen, row.kind, row.columns),
            "source": "project" if row.project_id else "company"}
    return out


# ---------------------------------------------------------------- requests

def screen_for_path(path):
    """The catalog screen that owns an API path (/api/v1/masters/wards/...)."""
    from app.middleware.module_permission_middleware import _module_from_path, _route_resource_from_path

    module = _module_from_path(path or "")
    if not module:
        return None
    owner = catalog.ROUTE_OWNERS.get(f"{module}/{_route_resource_from_path(path, module)}")
    return owner[1] if owner else None


def request_project(request, value=None):
    """The project a request works in: the one it names, else the user's own."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value not in (None, ""):
        return str(value)
    return str(getattr(getattr(request, "user", None), "project_id", "") or "")


def effective_columns(request, screen, project_id, kind):
    """The columns a request may use: the setting minus the fields the project
    may not have; with no setting but blocked fields, every other field.
    None = every field (nothing configured, nothing blocked)."""
    company = getattr(request, "company", None)
    columns, _ = resolve(company, project_id, screen, kind)
    blocked = set() if _sees_everything(request) else blocked_for(company, project_id, screen, kind)
    fields = (screen_fields(screen) or {}).get(kind, [])
    if columns:
        chosen = [c for c in normalize_columns(screen, kind, columns) if c["field"] not in blocked]
    elif blocked:
        chosen = [{"field": f["name"], "header": f["label"]} for f in fields if f["name"] not in blocked]
    else:
        return None
    if kind == EXPORT:
        # write what the list shows (continent_id -> continent_name)
        source = {f["name"]: f.get("source") or f["name"] for f in fields}
        chosen = [{"field": source.get(c["field"], c["field"]), "header": c["header"]} for c in chosen]
    return chosen


def export_columns(request, path, params, requested=None):
    """[{header, field}] an Excel export of `path` must write, or None (every field).
    Columns the page asked for are kept when nothing is configured for the
    screen, minus fields the project may not have."""
    screen = screen_for_path(path)
    project_id = request_project(request, (params or {}).get("project_id"))
    company = getattr(request, "company", None)
    if requested and resolve(company, project_id, screen, EXPORT)[0] is None:
        blocked = set() if _sees_everything(request) else blocked_for(company, project_id, screen, EXPORT)
        return [c for c in requested if not _field_keys(c["field"].split(".")[0]) & blocked]
    columns = effective_columns(request, screen, project_id, EXPORT)
    return [{"header": c["header"], "field": c["field"]} for c in columns] if columns is not None else None


def filter_upload_csv(raw, allowed, known):
    """The CSV text without the columns that are known fields (or a name
    variant of one, see field_variants) but not allowed."""
    base = {}
    for name in known:
        for variant in field_variants(name):
            if variant == name or variant not in known:
                base.setdefault(variant, name)
    reader = csv.reader(io.StringIO(raw))
    rows = list(reader)
    if not rows:
        return raw, []
    header = rows[0]

    def kept(column):
        field = base.get(column.strip())
        return field is None or field in allowed or column.strip() in TENANCY_KEYS

    keep = [i for i, name in enumerate(header) if kept(name)]
    removed = [header[i] for i in range(len(header)) if i not in keep]
    if not removed:
        return raw, []
    out = io.StringIO()
    writer = csv.writer(out)
    for row in rows:
        writer.writerow([row[i] if i < len(row) else "" for i in keep])
    return out.getvalue(), removed


class ImportColumnsMiddleware:
    """Bulk uploads (POST .../bulk-upload/, a CSV `file`) keep only the
    columns the company's / project's import setting enables. Columns that
    are not fields of the screen (page-specific names) pass through: the
    view ignores what it doesn't know. Runs after ModulePermissionMiddleware,
    so request.user is the signed-in staff member."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.method != "POST" or not request.path.rstrip("/").endswith("bulk-upload"):
            return None
        company = getattr(request, "company", None)
        screen = screen_for_path(request.path)
        if company is None or screen is None:
            return None
        columns = effective_columns(request, screen, request_project(
            request, request.POST.get("project_id") or request.GET.get("project_id")), IMPORT)
        if columns is None:
            return None
        upload = request.FILES.get("file")
        if upload is None or not (upload.name or "").lower().endswith(".csv"):
            return None
        try:
            raw = upload.read().decode("utf-8-sig")
        except (UnicodeDecodeError, ValueError):
            upload.seek(0)
            return None  # the view reports the bad file itself
        known = {f["name"] for f in screen_fields(screen)[IMPORT]}
        text, removed = filter_upload_csv(raw, {c["field"] for c in columns}, known)
        if removed:
            logger.info("Bulk upload to %s: dropped disabled columns %s", screen, removed)
        request.FILES["file"] = SimpleUploadedFile(upload.name, text.encode("utf-8"), content_type="text/csv")
        return None

"""Forms (IWMS screens) of the built-in modules, their fields, and which
fields each company uses.

A built-in module is an IWMS permission-catalog module; its screens are the
forms (Common Masters -> Continents, Countries, States). A form's fields are
the columns of the IWMS model behind the screen.

Per company, the super admin picks the fields its users see. They are stored
the way IWMS already stores field visibility: CompanyUserScreenColumnPermission
rows in the company DB, company-wide (project_id NULL), one per field with
can_view on/off. A form with no rows shows every field (IWMS default). The
rows reach company logins through their permission payload
(screen_grants.tenancy_column_rows), and the IWMS screens hide the fields that
are off (hooks/useFieldVisibility).
"""
import logging
from types import SimpleNamespace

from django.db import transaction
from rest_framework.exceptions import ValidationError

from app.models.superadmin.screen_management.companyuserscreencolumnpermission import (
    CompanyUserScreenColumnPermission,
)
from app.models.superadmin.screen_management.userscreen import UserScreen
from app.models.superadmin.screen_management.userscreencolumn import UserScreenColumn
from app.utils import permission_catalog as catalog
from app.utils.model_mapper import iter_model_columns, resolve_userscreen_model

from .context import tenant_context
from .db import register_tenant_db

logger = logging.getLogger(__name__)


# Audit/system columns no form shows (the "Active" switch, is_active, stays).
HIDDEN_FIELDS = {
    "is_deleted", "delete_reason", "created_by_id", "updated_by_id", "created_at", "updated_at",
    "deleted_at", "deleted_by_id", "password", "previous_password", "fcm_token",
}


def _form_fields(model):
    return [f for f in iter_model_columns(model) if f.name not in HIDDEN_FIELDS and not f.primary_key]


def _columns(alias, screen, values):
    """The screen's field rows. IWMS's schema sync links them by screen name
    (it passes the UserScreen object, whose str() is its name), older rows by
    unique_id: accept both."""
    return UserScreenColumn.objects.using(alias).filter(
        userscreen_id__in=[screen.pk, screen.userscreen_name], is_deleted=False).values_list(*values)


def _screen_model(screen_name):
    return resolve_userscreen_model(SimpleNamespace(
        userscreen_name=screen_name, folder_name="", model_app_label=None, model_name=None))


import re  # noqa: E402

SPECIAL_LABELS = {"is_active": "Status", "mob_code": "Mobile Code", "doj": "Date of Joining",
                  "emp_id": "Employee ID", "cp_name": "Collection Point Name", "sqft": "Sq. Ft."}


def friendly_label(name):
    """How a form names a model field: company_id -> Company, ward_ids -> Wards."""
    if name in SPECIAL_LABELS:
        return SPECIAL_LABELS[name]
    base = re.sub(r"_ids$", "s", name)
    base = re.sub(r"_id$", "", base)
    return base.replace("_", " ").strip().title() or name


def _field_json(field):
    verbose = str(getattr(field, "verbose_name", "") or "")
    explicit = verbose and verbose != field.name.replace("_", " ")
    return {
        "name": field.name,
        # As the form shows it: company_id -> Company (unless the model names it).
        "label": verbose.title() if explicit else friendly_label(field.name),
        "type": field.get_internal_type(),
        "required": not getattr(field, "null", False) and not getattr(field, "blank", False),
    }


def module_forms(code):
    """[{name, label, model, fields: [...]}] for a built-in module (catalog order)."""
    module = catalog.MODULES.get(code)
    if not module:
        return []
    forms = []
    for screen in module["screens"]:
        model = _screen_model(screen["name"])
        forms.append({
            "name": screen["name"],
            "label": screen["label"],
            "model": f"{model._meta.app_label}.{model.__name__}" if model else None,
            "superadmin_only": bool(screen.get("superadmin_only")),
            "fields": [_field_json(f) for f in _form_fields(model)] if model else [],
        })
    return forms


def _company_screens(alias, names):
    """{screen name: UserScreen} in the company DB, with its field rows synced."""
    from app.services.schema_sync_service import sync_userscreen_schema

    screens = {}
    with tenant_context(alias):
        for screen in UserScreen.objects.using(alias).filter(userscreen_name__in=names, is_deleted=False):
            if screen.userscreen_name in screens:
                continue
            try:
                sync_userscreen_schema(screen)  # field rows follow the model
            except Exception:
                logger.exception("Could not sync the fields of screen %s", screen.userscreen_name)
            screens[screen.userscreen_name] = screen
    return screens


def company_forms(company, code):
    """The module's forms with each field's enabled flag for this company."""
    alias = register_tenant_db(company.db_name)
    forms = [f for f in module_forms(code) if not f["superadmin_only"]]
    screens = _company_screens(alias, [f["name"] for f in forms])
    for form in forms:
        screen = screens.get(form["name"])
        form["available"] = screen is not None
        rows = {}
        if screen is not None:
            columns = dict(_columns(alias, screen, ("pk", "field_name")))
            for perm in CompanyUserScreenColumnPermission.objects.using(alias).filter(
                    company_id=company.unique_id, project_id__isnull=True, userscreen_id=screen.pk,
                    is_deleted=False):
                if perm.column_id in columns:
                    rows[columns[perm.column_id]] = perm.can_view and perm.is_active
        form["customised"] = bool(rows)
        for field in form["fields"]:
            field["enabled"] = rows.get(field["name"], not rows)
    return forms


def save_company_forms(company, code, data):
    """data: {screen name: [enabled field names] | None (= all fields)}."""
    if not isinstance(data, dict):
        raise ValidationError({"detail": "Send {form: [enabled field names] or null}."})
    alias = register_tenant_db(company.db_name)
    forms = {f["name"]: f for f in module_forms(code) if not f["superadmin_only"]}
    unknown = set(data) - set(forms)
    if unknown:
        raise ValidationError({"detail": f'Unknown form(s): {", ".join(sorted(unknown))}.'})
    screens = _company_screens(alias, list(data))
    with tenant_context(alias), transaction.atomic(using=alias):
        for name, enabled in data.items():
            screen = screens.get(name)
            if screen is None:
                raise ValidationError({"detail": f"{forms[name]['label']} is not set up in this company yet."})
            existing = CompanyUserScreenColumnPermission.objects.using(alias).filter(
                company_id=company.unique_id, project_id__isnull=True, userscreen_id=screen.pk)
            all_names = [f["name"] for f in forms[name]["fields"]]
            if enabled is None or set(enabled) >= set(all_names):
                existing.delete()  # every field (the IWMS default)
                continue
            if not isinstance(enabled, list) or set(enabled) - set(all_names):
                raise ValidationError({"detail": f"{forms[name]['label']}: unknown field in the list."})
            if not enabled:
                raise ValidationError({"detail": f"{forms[name]['label']}: keep at least one field."})
            columns = dict(_columns(alias, screen, ("field_name", "pk")))
            current = {p.column_id: p for p in existing}
            for order, field_name in enumerate(all_names, start=1):
                column_id = columns.get(field_name)
                if column_id is None:
                    continue
                on = field_name in enabled
                perm = current.get(column_id)
                if perm is None:
                    CompanyUserScreenColumnPermission.objects.using(alias).create(
                        company_id=company.unique_id, project_id=None, userscreen_id=screen.pk,
                        column_id=column_id, can_view=on, order_no=order)
                elif perm.can_view != on or perm.is_deleted or not perm.is_active:
                    CompanyUserScreenColumnPermission.objects.using(alias).filter(pk=perm.pk).update(
                        can_view=on, is_active=True, is_deleted=False)
    return company_forms(company, code)


# ---------------------------------------------------------------------------
# Form configuration (all companies): hide / rename / required / order of the
# IWMS fields, plus extra fields (FormConfig in the master DB).
# ---------------------------------------------------------------------------

def _configs(code=None):
    from app.models.tenancy.master import FormConfig

    qs = FormConfig.objects.all()
    if code:
        qs = qs.filter(module_code=code)
    return {c.form: c for c in qs}


def configured_forms(code):
    """module_forms(code) with each form's configuration applied:
    fields get label/required/hidden/order (and their IWMS defaults), and
    `extras` lists the added fields."""
    configs = _configs(code)
    forms = module_forms(code)
    for form in forms:
        cfg = configs.get(form["name"])
        overrides = (cfg.fields if cfg else {}) or {}
        for index, field in enumerate(form["fields"]):
            o = overrides.get(field["name"], {})
            field["default_label"], field["model_required"] = field["label"], field["required"]
            field["label"] = o.get("label") or field["label"]
            field["required"] = bool(o.get("required", field["required"]))
            field["hidden"] = bool(o.get("hidden", False))
            field["order"] = int(o.get("order", index + 1))
            field["_moved"] = 0 if "order" in o else 1
        # A field the admin placed wins a tie with an IWMS default position.
        form["fields"].sort(key=lambda f: (f["order"], f["_moved"]))
        for field in form["fields"]:
            field.pop("_moved", None)
        form["extras"] = (cfg.extras if cfg else []) or []
    return forms


def save_module_forms(code, data):
    """data: {form: {"fields": {name: {label, required, hidden, order}}, "extras": [...]}}"""
    from app.models.tenancy.master import FormConfig

    from .serializers import clean_field_definitions

    if not isinstance(data, dict):
        raise ValidationError({"detail": "Send {form: {fields, extras}}."})
    forms = {f["name"]: f for f in module_forms(code)}
    for name, cfg in data.items():
        form = forms.get(name)
        if form is None:
            raise ValidationError({"detail": f"Unknown form {name!r}."})
        if not isinstance(cfg, dict):
            raise ValidationError({"detail": f"{form['label']}: invalid settings."})
        model_fields = {f["name"]: f for f in form["fields"]}
        fields = {}
        for field_name, o in (cfg.get("fields") or {}).items():
            base = model_fields.get(field_name)
            if base is None or not isinstance(o, dict):
                raise ValidationError({"detail": f"{form['label']}: unknown field {field_name!r}."})
            clean = {}
            label = (o.get("label") or "").strip()[:100]
            if label and label != base["label"]:
                clean["label"] = label
            # The super admin decides: any field can be made required or
            # optional, and any field can be deleted (hidden).
            if "required" in o and bool(o["required"]) != base["required"]:
                clean["required"] = bool(o["required"])
            if o.get("hidden"):
                clean["hidden"] = True
            if "order" in o:
                clean["order"] = int(o["order"])
            if clean:
                fields[field_name] = clean
        extras = cfg.get("extras") or []
        if extras:
            try:
                extras = clean_field_definitions(extras)
            except Exception as exc:
                raise ValidationError({"detail": f"{form['label']}: {getattr(exc, 'detail', exc)}"})
            clash = {e["name"] for e in extras} & set(model_fields)
            if clash:
                raise ValidationError({"detail": (
                    f'{form["label"]}: {", ".join(sorted(clash))} is already a field of this form.')})
        FormConfig.objects.update_or_create(module_code=code, form=name,
                                            defaults={"fields": fields, "extras": extras})
    return configured_forms(code)



def screen_routes():
    """{screen name: [api route prefixes]} from the permission catalog."""
    out = {}
    for route, (_module, screen) in catalog.ROUTE_OWNERS.items():
        out.setdefault(screen, []).append(route.strip("/") + "/")
    return out


def company_form_config():
    """What the IWMS screens of a company need: per configured form its
    module, API routes, field overrides and extra fields."""
    routes = screen_routes()
    out = {}
    for name, cfg in _configs().items():
        if not cfg.fields and not cfg.extras:
            continue
        out[name] = {"module": cfg.module_code, "routes": routes.get(name, []),
                     "fields": cfg.fields or {}, "extras": cfg.extras or []}
    return out


def apply_hidden_fields(column_permissions):
    """Hide the fields hidden for all companies, in a column-permission
    payload ({grouped, flat}). IWMS shows only listed fields once a screen has
    entries, so a screen without entries gets one entry per field."""
    hidden_by_form = {}
    for name, cfg in _configs().items():
        hidden = {f for f, o in (cfg.fields or {}).items() if o.get("hidden")}
        if hidden:
            hidden_by_form[name] = (cfg.module_code, hidden)
    if not hidden_by_form:
        return column_permissions
    payload = column_permissions or {"grouped": {}, "flat": []}
    grouped, flat = payload.setdefault("grouped", {}), payload.setdefault("flat", [])
    for form_name, (module_code, hidden) in hidden_by_form.items():
        existing = [row for rows in grouped.values() for s, rs in rows.items() if s == form_name for row in rs]
        if existing:
            for row in existing:
                if row.get("fieldName") in hidden:
                    row["canView"] = False
            continue
        form = next((f for f in module_forms(module_code) if f["name"] == form_name), None)
        if form is None:
            continue
        rows = []
        for order, field in enumerate(form["fields"], start=1):
            rows.append({
                "uniqueId": f"form-config:{form_name}:{field['name']}", "companyId": None, "projectId": None,
                "mainScreenName": module_code, "mainScreenKey": module_code, "userScreenName": form_name,
                "screenKey": form_name, "fieldName": field["name"], "displayName": field["label"],
                "dataType": field["type"], "dbColumn": field["name"], "canView": field["name"] not in hidden,
                "isRequired": field["required"], "orderNo": order,
            })
        grouped.setdefault(module_code, {})[form_name] = rows
        flat.extend(rows)
    return payload


def required_errors(form_name, body, creating):
    """{field: [message]} for fields made required in the form config that
    the request leaves empty (PATCH: only the fields it sends)."""
    cfg = _configs().get(form_name)
    if cfg is None or not isinstance(body, dict):
        return {}
    errors = {}
    for field_name, o in (cfg.fields or {}).items():
        if not o.get("required") or o.get("hidden"):
            continue
        if not creating and field_name not in body:
            continue
        value = body.get(field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors[field_name] = [f"{o.get('label') or field_name.replace('_', ' ').title()} is required."]
    return errors

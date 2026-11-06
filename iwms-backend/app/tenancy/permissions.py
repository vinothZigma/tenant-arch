"""Company permissions for the IWMS screens, set by the super admin.

The same tables IWMS's own "company-wise screen permission" page uses, in the
company DB, per project:
  CompanyUserScreenPermission       company × project × screen × action
  CompanyUserScreenColumnPermission company × project × screen × field (can_view)

Console → Companies → (company) → Permissions shows them as
module → sub-module (screen) → actions + fields. A company admin gets exactly
the actions granted for the projects they run (screen_grants.py); fields with
rows show only when ticked. Staff keep IWMS's own staff access configuration.
"""
from django.db import transaction
from rest_framework.exceptions import ValidationError

from app.models.superadmin.screen_management.companyuserscreencolumnpermission import (
    CompanyUserScreenColumnPermission,
)
from app.models.superadmin.screen_management.companyuserscreenpermission import CompanyUserScreenPermission
from app.models.superadmin.screen_management.mainscreen import MainScreen
from app.models.superadmin.screen_management.userscreen import UserScreen
from app.models.superadmin.screen_management.userscreenaction import UserScreenAction
from app.models.superadmin_masters.project import Project
from app.utils import permission_catalog as catalog

from .context import tenant_context
from .db import register_tenant_db
from .iwms_forms import _columns, _company_screens, configured_forms, friendly_label  # noqa: F401

ACTIONS = ("view", "add", "edit", "delete", "use", "import", "export")
ACTION_LABELS = {"view": "View", "add": "Add", "edit": "Edit", "delete": "Delete", "use": "Use",
                 "import": "Import", "export": "Export"}
def _offered(screen):
    return [a for a in ACTIONS if a in (screen.get("actions") or catalog.SCREEN_ACTIONS.get(screen["name"]) or ACTIONS)]


def _projects(alias):
    return list(Project.objects.using(alias).filter(is_deleted=False).order_by("name").values("unique_id", "name"))


def company_permissions(company, project_id):
    """{projects, project, modules: [{code, name, screens: [{name, label, actions, fields}]}]}"""
    alias = register_tenant_db(company.db_name)
    projects = _projects(alias)
    if not projects:
        return {"projects": [], "project": None, "modules": []}
    if project_id not in {p["unique_id"] for p in projects}:
        project_id = projects[0]["unique_id"]
    action_names = dict(UserScreenAction.objects.using(alias).filter(is_deleted=False)
                        .values_list("unique_id", "action_name"))
    screen_names = dict(UserScreen.objects.using(alias).filter(is_deleted=False)
                        .values_list("unique_id", "userscreen_name"))
    granted = {}
    for row in CompanyUserScreenPermission.objects.using(alias).filter(
            company_id=company.unique_id, project_id=project_id, permission_type="screen", is_deleted=False):
        screen, action = screen_names.get(row.userscreen_id), action_names.get(row.userscreenaction_id)
        if screen and action:
            granted.setdefault(screen, set()).add(action.lower())

    from .iwms_tables import HIDDEN_MODULES

    modules = []
    for code, module in catalog.MODULES.items():
        if code in HIDDEN_MODULES:
            continue
        forms = {f["name"]: f for f in configured_forms(code)}
        screens = [s for s in module["screens"] if not s.get("superadmin_only")]
        if not screens:
            continue
        db_screens = _company_screens(alias, [s["name"] for s in screens])
        entries = []
        for screen in screens:
            db_screen = db_screens.get(screen["name"])
            field_rows = {}
            if db_screen is not None:
                columns = dict(_columns(alias, db_screen, ("pk", "field_name")))
                for perm in CompanyUserScreenColumnPermission.objects.using(alias).filter(
                        company_id=company.unique_id, project_id=project_id, userscreen_id=db_screen.pk,
                        is_deleted=False):
                    if perm.column_id in columns:
                        field_rows[columns[perm.column_id]] = perm.can_view and perm.is_active
            form = forms.get(screen["name"], {"fields": []})
            fields = [{
                "name": f["name"],
                "label": f["label"],
                "required": f["required"],   # shown ticked by default; can be unticked
                "enabled": field_rows.get(f["name"], not field_rows),
            } for f in form["fields"] if not f.get("hidden")]
            entries.append({
                "name": screen["name"], "label": screen["label"], "available": db_screen is not None,
                "actions": [{"name": a, "label": ACTION_LABELS[a], "granted": a in granted.get(screen["name"], ())}
                            for a in _offered(screen)],
                "fields": fields,
            })
        modules.append({"code": code, "name": module["label"], "screens": entries})
    return {"projects": [{"id": p["unique_id"], "name": p["name"]} for p in projects],
            "project": project_id, "modules": modules}


def save_company_permissions(company, data):
    """data: {project, actions: {screen: [actions]}, fields: {screen: [enabled] | null}}.
    Only the screens listed are changed."""
    alias = register_tenant_db(company.db_name)
    project_id = data.get("project")
    if not Project.objects.using(alias).filter(pk=project_id, is_deleted=False).exists():
        raise ValidationError({"project": "Choose one of the company's projects."})
    by_name = {s["name"]: (code, s) for code, m in catalog.MODULES.items() for s in m["screens"]}
    actions_in = data.get("actions") or {}
    fields_in = data.get("fields") or {}
    if not isinstance(actions_in, dict) or not isinstance(fields_in, dict):
        raise ValidationError({"detail": "Invalid permissions."})
    unknown = (set(actions_in) | set(fields_in)) - set(by_name)
    if unknown:
        raise ValidationError({"detail": f'Unknown screen(s): {", ".join(sorted(unknown))}.'})
    screens = _company_screens(alias, list(set(actions_in) | set(fields_in)))
    action_ids = {name.lower(): pk for pk, name in UserScreenAction.objects.using(alias).filter(
        is_deleted=False).values_list("unique_id", "action_name")}
    mains = dict(MainScreen.objects.using(alias).filter(is_deleted=False).values_list("mainscreen_name", "unique_id"))

    with tenant_context(alias), transaction.atomic(using=alias):
        for name, wanted in actions_in.items():
            code, screen = by_name[name]
            db_screen = screens.get(name)
            if db_screen is None:
                raise ValidationError({"detail": f"{screen['label']} is not set up in this company yet."})
            wanted = [a for a in (wanted or []) if a in _offered(screen)]
            if any(a != "view" for a in wanted) and "view" not in wanted:
                wanted.append("view")   # can't add or edit what you can't see
            rows = CompanyUserScreenPermission.objects.using(alias).filter(
                company_id=company.unique_id, project_id=project_id, userscreen_id=db_screen.pk,
                permission_type="screen", is_deleted=False)
            have = {r.userscreenaction_id: r for r in rows}
            keep = {action_ids[a] for a in wanted if a in action_ids}
            for action_id, row in have.items():
                if action_id not in keep:
                    CompanyUserScreenPermission.objects.using(alias).filter(pk=row.pk).update(
                        is_active=False, is_deleted=True)
            for order, action_id in enumerate(sorted(keep - set(have)), start=1):
                CompanyUserScreenPermission.objects.using(alias).create(
                    company_id=company.unique_id, project_id=project_id,
                    mainscreen_id=db_screen.mainscreen_id or mains.get(code), userscreen_id=db_screen.pk,
                    userscreenaction_id=action_id, permission_type="screen", order_no=order)
        for name, enabled in fields_in.items():
            _code, screen = by_name[name]
            db_screen = screens.get(name)
            if db_screen is None:
                continue
            existing = CompanyUserScreenColumnPermission.objects.using(alias).filter(
                company_id=company.unique_id, project_id=project_id, userscreen_id=db_screen.pk)
            columns = dict(_columns(alias, db_screen, ("field_name", "pk")))
            if enabled is None or set(enabled) >= set(columns):
                existing.delete()  # every field
                continue
            current = {p.column_id: p for p in existing}
            for order, (field_name, column_id) in enumerate(columns.items(), start=1):
                on = field_name in enabled
                perm = current.get(column_id)
                if perm is None:
                    CompanyUserScreenColumnPermission.objects.using(alias).create(
                        company_id=company.unique_id, project_id=project_id, userscreen_id=db_screen.pk,
                        column_id=column_id, can_view=on, order_no=order)
                elif perm.can_view != on or perm.is_deleted or not perm.is_active:
                    CompanyUserScreenColumnPermission.objects.using(alias).filter(pk=perm.pk).update(
                        can_view=on, is_active=True, is_deleted=False)
    return company_permissions(company, project_id)

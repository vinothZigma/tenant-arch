"""IWMS screen permissions of company admins.

The super admin grants a company its IWMS screens per project (Console →
Companies → (company) → Permissions, stored in IWMS's own
CompanyUserScreenPermission / CompanyUserScreenColumnPermission tables, see
permissions.py). A company admin gets exactly the actions granted for the
projects they run (the developer: every project), plus the field settings of
those screens. These are merged into the IWMS permission payload
(app/utils/permission_response.py), so the sidebar, the screens and the API
middleware agree. Staff keep IWMS's own staff access configuration.
"""
from app.utils import permission_catalog as catalog

from .context import get_current_tenant_db
from .router import per_db_mode

ACTION_MAP = {
    "view": ("view", "use", "export"),
    "create": ("add", "import"),
    "edit": ("edit",),
    "delete": ("delete",),
}
ALL_ACTIONS = ("view", "add", "edit", "delete", "use", "import", "export")


def module_actions(staff, alias):
    """{module_code: {"view", "create", ...}} the login has in any active project."""
    from app.models.superadmin_masters.project import Project
    from app.models.tenancy.tenant import ModulePermission, ProjectAdmin, ProjectModule

    live = set(Project.objects.using(alias).filter(is_deleted=False, is_active=True).values_list("pk", flat=True))
    caps = {}
    for pm in ProjectModule.objects.using(alias).filter(project_id__in=live):
        caps[(pm.project_id, pm.module_code)] = {a for a, on in pm.permissions.items() if on}

    out = {}
    if staff.is_company_admin:
        if staff.is_developer:
            projects = live
        else:
            projects = set(ProjectAdmin.objects.using(alias).filter(staff=staff).values_list("project_id", flat=True))
        for (pid, code), allowed in caps.items():
            if pid in projects:
                out.setdefault(code, set()).update(allowed)
        return out
    for row in ModulePermission.objects.using(alias).filter(staff=staff, project_id__in=live):
        allowed = caps.get((row.project_id, row.module_code), set())
        granted = {a for a in ACTION_MAP if getattr(row, f"can_{a}")} & allowed
        if granted:
            out.setdefault(row.module_code, set()).update(granted)
    return out


def admin_projects(staff, alias):
    """Projects a company admin runs (the developer: all of them)."""
    from app.models.superadmin_masters.project import Project
    from app.models.tenancy.tenant import ProjectAdmin

    live = set(Project.objects.using(alias).filter(is_deleted=False, is_active=True).values_list("pk", flat=True))
    if staff.is_developer:
        return live
    return live & set(ProjectAdmin.objects.using(alias).filter(staff=staff).values_list("project_id", flat=True))


def tenancy_screen_grants(staff_unique_id):
    """{main_screen: {screen: [actions]}} for a company admin: what the super
    admin granted the company (Console → Companies → Permissions) in the
    projects the admin runs. Staff keep IWMS's own staff access configuration."""
    alias = get_current_tenant_db()
    if not staff_unique_id or not per_db_mode() or not alias:
        return {}
    from app.models.superadmin.screen_management.companyuserscreenpermission import CompanyUserScreenPermission
    from app.models.superadmin.screen_management.mainscreen import MainScreen
    from app.models.superadmin.screen_management.userscreen import UserScreen
    from app.models.superadmin.screen_management.userscreenaction import UserScreenAction
    from app.models.superadmin.staff_management.staffcreation import Staffcreation

    staff = Staffcreation.objects.using(alias).filter(pk=staff_unique_id, is_deleted=False).first()
    if staff is None or not staff.is_company_admin:
        return {}
    projects = admin_projects(staff, alias)
    if not projects:
        return {}
    rows = CompanyUserScreenPermission.objects.using(alias).filter(
        company_id=staff.company_id, project_id__in=projects, permission_type="screen",
        is_active=True, is_deleted=False).values_list("mainscreen_id", "userscreen_id", "userscreenaction_id")
    mains = dict(MainScreen.objects.using(alias).values_list("unique_id", "mainscreen_name"))
    screens = dict(UserScreen.objects.using(alias).values_list("unique_id", "userscreen_name"))
    screen_mains = dict(UserScreen.objects.using(alias).values_list("unique_id", "mainscreen_id"))
    actions = dict(UserScreenAction.objects.using(alias).values_list("unique_id", "action_name"))
    grants = {}
    for main_id, screen_id, action_id in rows:
        main = mains.get(main_id or screen_mains.get(screen_id))
        screen, action = screens.get(screen_id), (actions.get(action_id) or "").lower()
        if main and screen and action:
            current = grants.setdefault(main, {}).setdefault(screen, [])
            if action not in current:
                current.append(action)
    return grants


def merge_permissions(base, extra):
    """Union of two {main: {screen: [actions]}} maps."""
    if not extra:
        return base
    merged = {main: {screen: list(acts) for screen, acts in screens.items()} for main, screens in base.items()}
    for main, screens in extra.items():
        target = merged.setdefault(main, {})
        for screen, actions in screens.items():
            current = target.setdefault(screen, [])
            current.extend(a for a in actions if a not in current)
    return merged


def tenancy_column_rows(company_unique_id, grants, staff=None):
    """The company-wide field settings (CompanyUserScreenColumnPermission,
    project_id NULL) of the screens in `grants` — see iwms_forms.py."""
    alias = get_current_tenant_db()
    if not grants or not company_unique_id or not alias:
        return None
    from app.models.superadmin.screen_management.companyuserscreencolumnpermission import (
        CompanyUserScreenColumnPermission,
    )
    from app.models.superadmin.screen_management.userscreen import UserScreen

    from django.db.models import Q

    names = {screen for screens in grants.values() for screen in screens}
    screen_ids = UserScreen.objects.using(alias).filter(
        userscreen_name__in=names, is_deleted=False).values_list("pk", flat=True)
    project_filter = Q(project_id__isnull=True)
    if staff is not None:
        project_filter |= Q(project_id__in=admin_projects(staff, alias))
    return CompanyUserScreenColumnPermission.objects.using(alias).filter(
        project_filter, company_id=company_unique_id, userscreen_id__in=list(screen_ids),
        is_active=True, is_deleted=False)


def merge_column_permissions(base, extra):
    """Union of two column-permission payloads ({grouped, flat})."""
    base = base or {"grouped": {}, "flat": []}
    if not extra or not extra.get("flat"):
        return base
    seen = {row.get("uniqueId") for row in base.get("flat", [])}
    flat = list(base.get("flat", []))
    grouped = {main: {s: list(rows) for s, rows in screens.items()} for main, screens in base.get("grouped", {}).items()}
    for row in extra["flat"]:
        if row.get("uniqueId") in seen:
            continue
        flat.append(row)
        if row.get("mainScreenName") and row.get("userScreenName"):
            grouped.setdefault(row["mainScreenName"], {}).setdefault(row["userScreenName"], []).append(row)
    return {"grouped": grouped, "flat": flat}

"""Make company logins work in the IWMS screens.

The IWMS sign-in needs a user type ("Staff") and a role (staff user type) on
every staff login, and reads which projects a login may open from its Staff
Access Configuration ("Data Scope"). sync_iwms_scopes() keeps both in step
with the projects:

* company admins (and the developer)  -> role "Company Admin", the projects
  they run (developer: all);
* staff with module access            -> role "Company User" when they have
  none, the projects they have access to.

Only configurations created here are updated (tagged MANAGED); one set up by
hand in the IWMS screens is left alone. Nobody gets an empty project list:
IWMS reads that as "every project".
"""
from app.models.superadmin.role_management.staffUserType import StaffUserType
from app.models.superadmin.role_management.userType import UserType
from app.models.superadmin.staff_management.staff_access_configuration import StaffAccessConfiguration
from app.models.superadmin.staff_management.staffcreation import Staffcreation
from app.models.superadmin_masters.project import Project

from .context import tenant_context

MANAGED = "Managed by project modules (multi-tenancy)"


def _by_name(model, alias, name):
    return model.objects.using(alias).filter(name__iexact=name, is_deleted=False).values_list("pk", flat=True).first()


def _staff_type(alias):
    """The "Staff" user type (from the shared catalog; created if missing)."""
    pk = _by_name(UserType, alias, "Staff")
    if pk is None:
        with tenant_context(alias):
            pk = UserType.objects.using(alias).create(name="Staff").pk
    return pk


def _role(alias, name, staff_type):
    pk = _by_name(StaffUserType, alias, name)
    if pk is None:
        with tenant_context(alias):
            pk = StaffUserType.objects.using(alias).create(name=name, usertype_id=staff_type).pk
    return pk


def iwms_role_defaults(staff, alias):
    """Fill a missing user type / role (no save). Returns the changed fields."""
    changed = []
    if not staff.user_type_id or not staff.staffusertype_id:
        staff_type = _staff_type(alias)
        if not staff.user_type_id:
            staff.user_type_id = staff_type
            changed.append("user_type_id")
        if not staff.staffusertype_id:
            role = "Company Admin" if staff.is_company_admin else "Company User"
            staff.staffusertype_id = _role(alias, role, staff_type)
            changed.append("staffusertype_id")
    return changed


def _wanted_projects(alias):
    """{staff_id: {project ids}} for admins and staff with module access."""
    from app.models.tenancy.tenant import ModulePermission, ProjectAdmin

    live = set(Project.objects.using(alias).filter(is_deleted=False).values_list("pk", flat=True))
    wanted = {}
    for staff_id, project_id in ProjectAdmin.objects.using(alias).values_list("staff_id", "project_id"):
        if project_id in live:
            wanted.setdefault(staff_id, set()).add(project_id)
    for staff in Staffcreation.objects.using(alias).filter(is_developer=True, is_deleted=False):
        wanted[staff.pk] = set(live)
    for staff_id, project_id in ModulePermission.objects.using(alias).filter(can_view=True).values_list(
            "staff_id", "project_id"):
        if project_id in live:
            wanted.setdefault(staff_id, set()).add(project_id)
    return wanted


def sync_iwms_scopes(alias, company):
    from .iwms_tables import missing_tables

    # Staff Access Configuration turned off for this company: no IWMS scope
    # rows (admins' projects then come from screen_grants.admin_project_scope).
    scopes_on = StaffAccessConfiguration._meta.db_table not in missing_tables(alias)
    wanted = _wanted_projects(alias)
    people = Staffcreation.objects.using(alias).filter(is_deleted=False).filter(pk__in=list(wanted)) | \
        Staffcreation.objects.using(alias).filter(is_deleted=False, is_company_admin=True)
    with tenant_context(alias):
        for staff in people.distinct():
            fields = iwms_role_defaults(staff, alias)
            if fields:
                Staffcreation.objects.using(alias).filter(pk=staff.pk).update(
                    **{f: getattr(staff, f) for f in fields})
            projects = sorted(wanted.get(staff.pk, ()))
            if not scopes_on:
                continue
            config = StaffAccessConfiguration.objects.using(alias).filter(
                staff_id=staff.pk, is_deleted=False).first()
            if config is None:
                if projects:
                    StaffAccessConfiguration.objects.using(alias).create(
                        staff_id=staff.pk, company_id=company.unique_id, project_ids=",".join(projects),
                        description=MANAGED)
            elif config.description == MANAGED:
                if projects and config.project_ids != ",".join(projects):
                    StaffAccessConfiguration.objects.using(alias).filter(pk=config.pk).update(
                        project_ids=",".join(projects))
                elif not projects:
                    # No project left: switch the managed scope off (empty = all projects).
                    StaffAccessConfiguration.objects.using(alias).filter(pk=config.pk).update(
                        is_active=False, is_deleted=True)

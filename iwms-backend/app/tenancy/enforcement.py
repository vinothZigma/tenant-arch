"""Plan limits and project caps for the built-in IWMS modules.

Built-in modules (staff, vehicles, bins, ...) are created through the
existing IWMS screens, so the checks sit around those routes:

PlanLimitMiddleware (after TenantMiddleware)
    * project caps: a write on a module's route (Module.api_paths) for a
      project that uses the module system but doesn't allow that action ->
      403. Projects without any ProjectModule rows (created before the
      module system) are not restricted.
    * record limit: a POST that creates a row of a limited module runs
      inside a transaction that first takes the module's lock row, so two
      parallel creates can't both pass the limit.

pre_save checks (connected in ApiConfig.ready)
    * record limit for any other way a row gets created (bulk upload, ...);
    * user limit when a staff login is switched on.
"""
import json
import time

from django.db import transaction
from django.db.models.signals import pre_save
from django.http import JsonResponse
from rest_framework.exceptions import PermissionDenied

from .context import get_current_tenant_db
from .modules import STAFF_MODULE
from .router import per_db_mode

_CACHE = {"at": 0.0, "modules": []}
_CACHE_SECONDS = 30
ACTION_BY_METHOD = {"GET": "view", "POST": "create", "PUT": "edit", "PATCH": "edit", "DELETE": "delete"}


def builtin_modules():
    """Built-in Module rows (cached briefly; master DB)."""
    from app.models.tenancy.master import Module

    now = time.monotonic()
    if now - _CACHE["at"] > _CACHE_SECONDS:
        _CACHE["modules"] = list(Module.objects.filter(kind=Module.BUILTIN, is_active=True))
        _CACHE["at"] = now
    return _CACHE["modules"]


def clear_cache():
    _CACHE["at"] = 0.0


def current_company():
    from app.models.superadmin_masters.company import Company

    alias = get_current_tenant_db()
    if not alias:
        return None
    return Company.objects.filter(db_name=alias, is_deleted=False).select_related("plan").first()


def _module_for_path(path):
    """(module, tail, is_main_route) for an /api/v1/ path. A module's first
    api_path is its main route: the one whose plain POST creates a counted
    record (e.g. Staff Management -> staff-creations/staffcreation/)."""
    rest = path[len("/api/v1/"):] if path.startswith("/api/v1/") else None
    if rest is None:
        return None, None, False
    best = None
    for module in builtin_modules():
        for i, prefix in enumerate(module.api_paths or []):
            if rest.startswith(prefix) and (best is None or len(prefix) > len(best[1])):
                best = (module, prefix, i == 0)
    if best is None:
        return None, None, False
    module, prefix, main = best
    return module, rest[len(prefix):].strip("/"), main


def _project_id(request):
    pid = request.GET.get("project_id")
    if not pid and request.method in ("POST", "PUT", "PATCH") and "json" in (request.content_type or ""):
        try:
            body = json.loads(request.body or b"{}")
            pid = body.get("project_id") if isinstance(body, dict) else None
        except (ValueError, UnicodeDecodeError):
            pid = None
    if not pid and request.method in ("POST", "PUT", "PATCH"):
        pid = request.POST.get("project_id")
    return pid if isinstance(pid, str) else None


def _cap_error(alias, module, project_id, action):
    from app.models.superadmin_masters.project import Project
    from app.models.tenancy.tenant import ProjectModule

    pms = ProjectModule.objects.using(alias).filter(project_id=project_id)
    if not pms.exists():
        return None  # project predates the module system
    pm = pms.filter(module_code=module.code).first()
    name = Project.objects.using(alias).filter(pk=project_id).values_list("name", flat=True).first() or "This project"
    if pm is None:
        return f"{module.name} is not part of {name}."
    if not pm.permissions[action]:
        return f"{name} does not allow {action} in {module.name}."
    return None


def _required_fields_error(request, action):
    """Fields the console made required (FormConfig) must be filled when an
    IWMS form saves (POST to a list route, PUT/PATCH to a record)."""
    if action not in ("create", "edit") or not request.path_info.startswith("/api/v1/"):
        return None
    from .iwms_forms import required_errors, screen_routes

    rest = request.path_info[len("/api/v1/"):]
    best = None
    for screen, prefixes in screen_routes().items():
        for prefix in prefixes:
            if rest.startswith(prefix) and (best is None or len(prefix) > len(best[1])):
                best = (screen, prefix)
    if best is None:
        return None
    tail = rest[len(best[1]):].strip("/")
    if (action == "create" and tail) or (action == "edit" and (not tail or "/" in tail)):
        return None
    if "json" in (request.content_type or ""):
        try:
            body = json.loads(request.body or b"{}")
        except (ValueError, UnicodeDecodeError):
            return None
    else:
        body = request.POST.dict()
    errors = required_errors(best[0], body, creating=action == "create" or request.method == "PUT")
    return JsonResponse(errors, status=400) if errors else None


def _staff_error(alias, module, project_id, action, request):
    """Staff (not company admins) need their admin's grant for this module in
    projects that use the module system."""
    from app.models.tenancy.tenant import ModulePermission, ProjectModule

    payload = getattr(request, "token_payload", None) or {}
    if _is_admin_request(request) or payload.get("user_type") not in ("staff", "contractor"):
        return None
    if not ProjectModule.objects.using(alias).filter(project_id=project_id).exists():
        return None  # project predates the module system
    grant = ModulePermission.objects.using(alias).filter(
        staff_id=payload.get("unique_id"), project_id=project_id, module_code=module.code).first()
    if grant is None or not getattr(grant, f"can_{action}"):
        return f"You do not have {action} permission for {module.name} in this project. Ask your company admin."
    return None


def _is_admin_request(request):
    user = getattr(request, "user", None)
    payload = getattr(request, "token_payload", None) or {}
    return bool(payload.get("company_admin")) or payload.get("scope") == "super" or bool(
        getattr(user, "is_company_admin", False))


class PlanLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        action = ACTION_BY_METHOD.get(request.method)
        alias = get_current_tenant_db()
        if not action or not alias or not per_db_mode():
            return self.get_response(request)
        error = _required_fields_error(request, action)
        if error:
            return error
        module, tail, main_route = _module_for_path(request.path_info)
        if module is None:
            return self.get_response(request)

        # What a company may do on its IWMS screens is the company permission
        # set in the console (screen_grants.py / IWMS's own checks); here
        # only the plan's record limits apply.

        # Only a plain POST to the main route creates a counted row.
        if action != "create" or tail or not main_route or not module.model_label:
            return self.get_response(request)
        company = current_company()
        if company is None or company.plan is None:
            return self.get_response(request)
        from .billing import record_room
        from .modules import ensure_lock_row

        ensure_lock_row(alias, module.code)
        try:
            with transaction.atomic(using=alias):
                record_room(company, alias, module, is_admin=_is_admin_request(request))
                response = self.get_response(request)
                if hasattr(response, "render") and not getattr(response, "is_rendered", True):
                    response.render()
                return response
        except PermissionDenied as exc:
            return JsonResponse({"detail": str(exc.detail)}, status=403)


def _check_builtin_limit(sender, instance, **kwargs):
    """pre_save: creating a counted row of a limited built-in module."""
    if not per_db_mode() or not instance._state.adding:
        return
    alias = get_current_tenant_db()
    if not alias:
        return
    label = f"{sender._meta.app_label}.{sender.__name__}".lower()
    module = next((m for m in builtin_modules() if m.model_label.lower() == label), None)
    if module is None:
        return
    if not getattr(instance, "is_active", True) or getattr(instance, "is_deleted", False):
        return
    if module.code == STAFF_MODULE and (getattr(instance, "is_company_admin", False) or getattr(instance, "is_developer", False)):
        return
    company = current_company()
    if company is None or company.plan is None:
        return
    from .billing import limit_message, module_quotas
    from .modules import builtin_qs

    quota = module_quotas(company, counts={}).get(module.code)
    if not quota or quota["limit"] is None:
        return
    used = builtin_qs(alias, module).count()
    if used >= quota["limit"]:
        raise PermissionDenied(limit_message(company, {**quota, "used": used}, is_admin=True))


def _check_user_limit(sender, instance, **kwargs):
    """pre_save on staff: switching a login on takes a plan user slot."""
    if not per_db_mode() or not instance.login_enabled or instance.is_developer or instance.is_deleted:
        return
    alias = get_current_tenant_db()
    if not alias:
        return
    if not instance._state.adding:
        before = type(instance).objects.using(alias).filter(pk=instance.pk).values_list(
            "login_enabled", "is_deleted").first()
        if before and before[0] and not before[1]:
            return  # already counted
    company = current_company()
    if company is None:
        return
    from .billing import user_room

    user_room(company, alias, 1)


def connect_signals():
    from app.models.superadmin.staff_management.staffcreation import Staffcreation

    pre_save.connect(_check_builtin_limit, dispatch_uid="tenancy.builtin_limit")
    pre_save.connect(_check_user_limit, sender=Staffcreation, dispatch_uid="tenancy.user_limit")

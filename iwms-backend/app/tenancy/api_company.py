"""Company workspace API: /api/t/<slug>/ws/...

TenantMiddleware already selected the company (request.company) and its DB
and refused tokens of other companies. Company users are IWMS staff logins.
"""
import datetime
from decimal import Decimal

from django.contrib.auth.hashers import check_password, make_password
from django.db import models, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from app.authentication.cookies import clear_auth_cookie, request_portal, set_auth_cookie
from app.models.superadmin.audits.loginAudit import LoginAudit
from app.models.superadmin.staff_management.staffcreation import Staffcreation
from app.models.superadmin_masters.project import Project
from app.models.tenancy.master import CompanyModule, ExtraRecordPurchase, Plan, PlanChangeRequest
from app.models.tenancy.tenant import ModulePermission, ProjectAdmin, ProjectModule

from .auth import CompanyUserAuthentication, IsCompanyAdmin, tenant_token
from .billing import (
    CURRENCY, company_usage, guarded_create, module_quotas, plan_json, record_room,
    request_extra_records, user_room,
)
from .db import register_tenant_db
from .logos import clean_theme_color, logo_url, remove_logo, set_logo
from .modules import STAFF_MODULE, builtin_model, ensure_lock_row, project_counts
from .module_tables import ModuleStore
from .people import check_not_reserved, check_password_rules, clean_email, login_json, username_taken
from .staff_access import refresh_staff_access
from .validation import clean_record_data

ACTIONS = ("view", "create", "edit", "delete")


def _plain(value):
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, models.fields.files.FieldFile):
        return value.name or None
    return value


class TenantView(APIView):
    authentication_classes = [CompanyUserAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def initial(self, request, *args, **kwargs):
        company = getattr(request, "company", None)
        if company is None:
            raise NotFound("Open this from the company's own address: /api/t/<company>/ws/...")
        request.tenant_db = register_tenant_db(company.db_name)
        super().initial(request, *args, **kwargs)

    # --- helpers ----------------------------------------------------------
    @property
    def db(self):
        return self.request.tenant_db

    def enabled_modules(self):
        """{code: CompanyModule} switched on for this company."""
        if not hasattr(self, "_enabled"):
            rows = CompanyModule.objects.filter(company=self.request.company, module__is_active=True) \
                .select_related("module")
            self._enabled = {cm.module.code: cm for cm in rows}
        return self._enabled

    def projects(self, include_archived=False):
        key = "_projects_all" if include_archived else "_projects"
        if not hasattr(self, key):
            qs = Project.objects.using(self.db).filter(is_deleted=False).order_by("name")
            if not include_archived:
                qs = qs.filter(is_active=True)
            setattr(self, key, list(qs))
        return getattr(self, key)

    def project_modules(self, project):
        if not hasattr(self, "_pms"):
            self._pms = {}
            for pm in ProjectModule.objects.using(self.db).all():
                self._pms.setdefault(pm.project_id, []).append(pm)
        enabled = self.enabled_modules()
        pms = [pm for pm in self._pms.get(project.pk, []) if pm.module_code in enabled]
        pms.sort(key=lambda pm: enabled[pm.module_code].module.name)
        return {pm.module_code: pm for pm in pms}

    def admin_project_ids(self, user=None, include_archived=True):
        user = user or self.request.user
        projects = self.projects(include_archived)
        if user.is_developer:
            return {p.pk for p in projects}
        mine = set(ProjectAdmin.objects.using(self.db).filter(staff=user).values_list("project_id", flat=True))
        return {p.pk for p in projects if p.pk in mine}

    def permissions_for(self, user):
        """{project_id: {code: {view, create, edit, delete}}} for active projects."""
        if user.is_company_admin:
            mine = self.admin_project_ids(user, include_archived=False)
            return {p.pk: {c: dict(pm.permissions) for c, pm in self.project_modules(p).items()}
                    for p in self.projects() if p.pk in mine}
        granted = {(r.project_id, r.module_code): r
                   for r in ModulePermission.objects.using(self.db).filter(staff=user)}
        out = {}
        for p in self.projects():
            out[p.pk] = {}
            for c, pm in self.project_modules(p).items():
                r = granted.get((p.pk, c))
                out[p.pk][c] = {a: bool(r and getattr(r, f"can_{a}")) and pm.permissions[a] for a in ACTIONS}
        return out

    def get_project(self, project_id):
        for p in self.projects():
            if str(p.pk) == str(project_id):
                return p
        raise NotFound("Project not found.")

    def require(self, project_id, code, act):
        project = self.get_project(project_id)
        cm = self.enabled_modules().get(code)
        if not cm or code not in self.project_modules(project):
            raise NotFound("Module not available in this project.")
        perms = self.permissions_for(self.request.user).get(project.pk)
        if perms is None:
            raise PermissionDenied(f"You do not have access to {project.name}.")
        if not perms[code][act]:
            raise PermissionDenied(f"You do not have {act} permission for {cm.module.name} in {project.name}.")
        return project, cm

    def visible_codes(self, user):
        return {c for mods in self.permissions_for(user).values() for c, p in mods.items() if p["view"]}

    def store(self, code):
        return ModuleStore(self.db, code, self.enabled_modules()[code].effective_fields)

    def usage(self):
        usage = company_usage(self.request.company)
        visible = self.visible_codes(self.request.user)
        usage["modules"] = {c: q for c, q in usage["modules"].items() if c in visible}
        return usage

    def usage_for_me(self):
        usage = self.usage()
        if self.request.user.is_company_admin:
            return {"usage": usage, "limits_reached": []}
        full = [c for c, q in usage["modules"].items() if q["limit"] is not None and q["used"] >= q["limit"]]
        return {"usage": None, "limits_reached": full}


def company_json(company):
    return {"name": company.name, "slug": company.slug, "contact_email": company.contact_email,
            "logo_url": logo_url(company), "theme_color": company.theme_color}


# --- public / auth ----------------------------------------------------------

class CompanyInfoView(TenantView):
    """For the /<slug>/login page."""
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({**company_json(request.company), "is_active": request.company.is_active})


class LoginView(TenantView):
    """Company login (staff logins of this company only)."""
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        username = (request.data.get("username") or "").strip()[:150]
        password = request.data.get("password") or ""
        user = Staffcreation.objects.using(self.db).filter(
            username__iexact=username, is_deleted=False).first() if username else None
        ok = bool(user and user.password and check_password(password, user.password))
        LoginAudit.objects.using(self.db).create(
            user_unique_id=user.pk if ok else None, username=username, password="",
            ip_address=request.META.get("REMOTE_ADDR") or None, user_agent=request.META.get("HTTP_USER_AGENT", "")[:255],
            success=ok and user.login_enabled, reason=None if ok else "Invalid credentials",
            company_id=request.company.unique_id)
        if not ok:
            return Response({"detail": "Invalid username or password."}, status=400)
        if not user.login_enabled:
            return Response({"detail": "Your login is switched off. Ask your company admin."}, status=400)
        Staffcreation.objects.using(self.db).filter(pk=user.pk).update(
            last_login_at=timezone.now(), failed_login_attempts=0)
        token, max_age = tenant_token(user, request.company)
        body = {"user": login_json(user)}
        portal = request_portal(request)
        if portal is None:
            body["token"] = token
        response = Response(body)
        if portal:
            set_auth_cookie(response, portal, token, max_age=max_age)
        return response


class LogoutView(TenantView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        response = Response(status=204)
        portal = request_portal(request)
        if portal:
            clear_auth_cookie(response, portal)
        return response


class MeView(TenantView):
    """Who am I, my projects and their modules, plan usage."""

    def get(self, request):
        enabled = self.enabled_modules()
        perms = self.permissions_for(request.user)
        mods = [cm.module for cm in enabled.values()]
        counts = project_counts(self.db, mods)
        counted = project_counts(self.db, mods, counted_only=True)
        projects = []
        for p in self.projects():
            if p.pk not in perms:
                continue
            pm = [{"code": c, "permissions": perms[p.pk][c], "records": counts.get(p.pk, {}).get(c, 0),
                   "counted": counted.get(p.pk, {}).get(c, 0)}
                  for c in self.project_modules(p) if perms[p.pk][c]["view"]]
            if pm or request.user.is_company_admin:
                projects.append({"id": p.pk, "name": p.name, "description": p.description or "", "modules": pm})
        visible = self.visible_codes(request.user)
        modules = [
            {"code": code, "name": cm.module.name, "description": cm.module.description,
             "kind": cm.module.kind, "fields": cm.effective_fields}
            for code, cm in enabled.items() if code in visible
        ]
        return Response({
            "company": company_json(request.company),
            "user": login_json(request.user),
            "viewed_by_super_admin": (request.auth or {}).get("imp"),
            "projects": projects,
            "modules": modules,
            "company_modules": [{"code": c, "name": cm.module.name, "kind": cm.module.kind}
                                for c, cm in sorted(enabled.items(), key=lambda kv: kv[1].module.name)],
            **self.usage_for_me(),
        })


class ProfileView(TenantView):
    def patch(self, request):
        user = request.user
        if "full_name" in request.data:
            name = (request.data.get("full_name") or "").strip()[:200]
            if not name:
                raise ValidationError({"full_name": "Enter your name."})
            user.employee_name = name
        if "email" in request.data:
            user.office_email = clean_email(request.data.get("email")) or None
        user.updated_by_id = user.pk
        user.save(update_fields=["employee_name", "office_email", "updated_by_id", "updated_at"])
        refresh_staff_access(self.db)
        return Response(login_json(user))


class PasswordView(TenantView):
    def post(self, request):
        user = request.user
        if not check_password(request.data.get("current_password") or "", user.password or ""):
            raise ValidationError({"detail": "Current password is incorrect."})
        new = request.data.get("new_password") or ""
        check_password_rules(new, "detail")
        if new == request.data.get("current_password"):
            raise ValidationError({"detail": "New password must be different from the current one."})
        user.previous_password = user.password
        user.password = make_password(new)
        user.password_crt_date = timezone.now()
        user.save(update_fields=["password", "previous_password", "password_crt_date", "updated_at"])
        return Response(status=204)


# --- records ------------------------------------------------------------------

def builtin_rows(view, cm, project, deleted=False):
    if not cm.module.model_label:
        return []   # e.g. Dashboard, Reports: no records
    model = builtin_model(cm.module)
    qs = model.objects.using(view.db).filter(project_id=project.pk, is_deleted=deleted)
    if cm.module.code == STAFF_MODULE:
        qs = qs.filter(is_developer=False, is_company_admin=False)
    return qs.order_by("-created_at")[:500]


def builtin_json(cm, row):
    return {"id": row.pk, "project_id": row.project_id,
            "data": {f["name"]: _plain(getattr(row, f["name"], None)) for f in cm.effective_fields},
            "is_active": row.is_active, "is_deleted": row.is_deleted,
            "created_at": getattr(row, "created_at", None), "updated_at": getattr(row, "updated_at", None),
            "deleted_at": getattr(row, "deleted_at", None),
            "created_by_id": row.created_by_id, "updated_by_id": row.updated_by_id}


def read_only_builtin(cm):
    raise ValidationError({"detail": (
        f"{cm.module.name} is a built-in IWMS module: add, edit and delete its records in the IWMS screens "
        "(sign in at /auth with the same login).")})


class RecordListView(TenantView):
    def get(self, request, project_id, module_code):
        project, cm = self.require(project_id, module_code, "view")
        if cm.module.is_builtin:
            return Response([builtin_json(cm, r) for r in builtin_rows(self, cm, project)])
        store = self.store(module_code)
        return Response([store.to_json(r) for r in store.qs().filter(project_id=project.pk).order_by("-id")])

    def post(self, request, project_id, module_code):
        project, cm = self.require(project_id, module_code, "create")
        if cm.module.is_builtin:
            read_only_builtin(cm)
        data = clean_record_data(cm.effective_fields, request.data.get("data", {}))
        store = self.store(module_code)
        is_active = bool(request.data.get("is_active", True))

        def create():
            return store.create(project.pk, data, user_id=request.user.pk, is_active=is_active)

        if is_active:
            row = guarded_create(request.company, self.db, cm.module, create,
                                 is_admin=request.user.is_company_admin)
        else:
            row = create()
        return Response(store.to_json(row), status=201)


class RecordDetailView(TenantView):
    def get_row(self, store, project, pk):
        row = store.qs().filter(pk=pk, project_id=project.pk).first()
        if not row:
            raise NotFound("Record not found.")
        return row

    def put(self, request, project_id, module_code, pk):
        project, cm = self.require(project_id, module_code, "edit")
        if cm.module.is_builtin:
            read_only_builtin(cm)
        store = self.store(module_code)
        row = self.get_row(store, project, pk)
        data = clean_record_data(cm.effective_fields, request.data.get("data", {}))
        ensure_lock_row(self.db, module_code)
        with transaction.atomic(using=self.db):
            if request.data.get("is_active") is True and not row.is_active:
                record_room(request.company, self.db, cm.module, is_admin=request.user.is_company_admin)
            store.update(row, data, user_id=request.user.pk, is_active=request.data.get("is_active"))
        return Response(store.to_json(row))

    def delete(self, request, project_id, module_code, pk):
        project, cm = self.require(project_id, module_code, "delete")
        if cm.module.is_builtin:
            read_only_builtin(cm)
        store = self.store(module_code)
        store.soft_delete(self.get_row(store, project, pk), user_id=request.user.pk)
        return Response(status=204)


# --- users & permissions (company admin) --------------------------------------

def has_login(staff):
    return bool(staff.username) and bool(staff.password)


class UserAdminView(TenantView):
    permission_classes = [IsAuthenticated, IsCompanyAdmin]

    def my_projects(self):
        mine = self.admin_project_ids()
        return [p for p in self.projects(include_archived=True) if p.pk in mine]

    def staff_rows(self):
        return Staffcreation.objects.using(self.db).filter(
            is_deleted=False, is_company_admin=False, is_developer=False)

    def staff_scope(self, logins_only=True):
        """Staff this admin may see: employees of their projects, people with
        access to their projects, and logins not attached to any project."""
        mine = self.admin_project_ids()
        with_perms = set(ModulePermission.objects.using(self.db).filter(project_id__in=mine, can_view=True)
                         .values_list("staff_id", flat=True))
        anywhere = set(ModulePermission.objects.using(self.db).filter(can_view=True)
                       .values_list("staff_id", flat=True))
        out = []
        for s in self.staff_rows():
            if logins_only and not has_login(s):
                continue
            if s.project_id in mine or s.pk in with_perms or (not s.project_id and s.pk not in anywhere):
                out.append(s)
        return out

    def save_permissions(self, staff, perms):
        """perms: {project_id: {code: {view, create, edit, delete}}}; only
        projects this admin runs are touched, never above the project's caps."""
        actor_id = self.request.user.pk
        enabled = self.enabled_modules()
        projects = {str(p.pk): p for p in self.my_projects()}
        for pid, mods in (perms or {}).items():
            project = projects.get(str(pid))
            if not project or not isinstance(mods, dict):
                continue
            pms = self.project_modules(project)
            for code, p in mods.items():
                pm = pms.get(code)
                if not pm or not isinstance(p, dict):
                    continue
                denied = [a for a in ACTIONS if p.get(a) and not pm.permissions[a]]
                if denied:
                    raise ValidationError({"permissions": (
                        f"{project.name} › {enabled[code].module.name}: this project does not allow "
                        f'{", ".join(denied)}, so it cannot be given to staff.')})
                flags = {f"can_{a}": bool(p.get(a)) for a in ACTIONS}
                if any(flags[f"can_{a}"] for a in ("create", "edit", "delete")):
                    flags["can_view"] = True
                if not flags["can_view"]:
                    ModulePermission.all_objects.using(self.db).filter(
                        staff=staff, project=project, module_code=code).delete()
                    continue
                live = {"is_deleted": False, "deleted_at": None, "deleted_by_id": None, "updated_by_id": actor_id}
                ModulePermission.all_objects.using(self.db).update_or_create(
                    staff=staff, project=project, module_code=code, defaults={**flags, **live},
                    create_defaults={**flags, **live, "created_by_id": actor_id})


def clean_employee(view, employee_id):
    """The staff row (employee) to give a login: in one of the admin's projects, no login yet."""
    if not employee_id:
        raise ValidationError({"employee_id": "Choose an employee."})
    staff = view.staff_rows().filter(pk=employee_id, project_id__in=view.admin_project_ids()).first()
    if staff is None:
        raise ValidationError({"employee_id": "Employee not found in your projects."})
    if has_login(staff):
        raise ValidationError({"employee_id": "This employee already has a login."})
    return staff


class UserListView(UserAdminView):
    def get(self, request):
        return Response([login_json(u, {"permissions": self.permissions_for(u)}) for u in self.staff_scope()])

    def post(self, request):
        d = request.data
        username = (d.get("username") or "").strip()
        password = d.get("password") or ""
        if not username:
            raise ValidationError({"username": "Required."})
        check_password_rules(password)
        check_not_reserved(username)
        if username_taken(self.db, username):
            raise ValidationError({"username": "This username is already taken."})
        staff = clean_employee(self, d.get("employee_id"))
        login_on = bool(d.get("is_active", True))
        with transaction.atomic(using=self.db):
            if login_on:
                user_room(request.company, self.db, 1)
            staff.username = username[:150]
            staff.password = make_password(password)
            staff.login_enabled = login_on
            staff.approval_status = Staffcreation.APPROVAL_APPROVED
            if d.get("full_name"):
                staff.employee_name = d["full_name"].strip()[:200]
            if "email" in d:
                staff.office_email = clean_email(d.get("email")) or None
            staff.updated_by_id = request.user.pk
            from .iwms_scope import iwms_role_defaults

            iwms_role_defaults(staff, self.db)
            staff.save()
            self.save_permissions(staff, d.get("permissions", {}))
        refresh_staff_access(self.db)
        return Response(login_json(staff, {"permissions": self.permissions_for(staff)}), status=201)


class UserDetailView(UserAdminView):
    def get_user(self, pk):
        for u in self.staff_scope():
            if str(u.pk) == str(pk):
                return u
        raise NotFound("Staff member not found.")

    def put(self, request, pk):
        user = self.get_user(pk)
        d = request.data
        if "full_name" in d and (d.get("full_name") or "").strip():
            user.employee_name = d["full_name"].strip()[:200]
        if "email" in d:
            user.office_email = clean_email(d.get("email")) or None
        if d.get("password"):
            check_password_rules(d["password"])
            user.password = make_password(d["password"])
        if "username" in d and (d.get("username") or "").strip() and d["username"].strip() != user.username:
            new = d["username"].strip()
            check_not_reserved(new)
            if username_taken(self.db, new, user.pk):
                raise ValidationError({"username": "This username is already taken."})
            user.username = new[:150]
        with transaction.atomic(using=self.db):
            if "is_active" in d:
                turning_on = bool(d["is_active"]) and not user.login_enabled
                if turning_on:
                    user_room(request.company, self.db, 1)
                user.login_enabled = bool(d["is_active"])
            user.updated_by_id = request.user.pk
            user.save()
            if "permissions" in d:
                self.save_permissions(user, d["permissions"])
        refresh_staff_access(self.db)
        return Response(login_json(user, {"permissions": self.permissions_for(user)}))

    def delete(self, request, pk):
        """Remove the login (the employee record stays)."""
        user = self.get_user(pk)
        mine = self.admin_project_ids()
        elsewhere = ModulePermission.objects.using(self.db).filter(
            staff=user, can_view=True).exclude(project_id__in=mine).exists()
        if elsewhere or (user.project_id and user.project_id not in mine):
            raise ValidationError({"detail": (
                "This person also works in projects run by another admin, so their login cannot be "
                "removed here. Use Edit access to take away their access to your projects.")})
        with transaction.atomic(using=self.db):
            user.username = None
            user.password = None
            user.login_enabled = False
            user.updated_by_id = request.user.pk
            user.save()
            ModulePermission.all_objects.using(self.db).filter(staff=user).delete()
        refresh_staff_access(self.db)
        return Response(status=204)


class StaffDirectoryView(UserAdminView):
    """Staff records (employees) of the projects this admin runs."""

    def get(self, request):
        names = {p.pk: p.name for p in self.my_projects()}
        rows = self.staff_rows().filter(project_id__in=list(names)).order_by("employee_name")
        return Response([{
            "id": s.pk, "project_id": s.project_id, "project_name": names.get(s.project_id),
            "data": {"name": s.employee_name, "emp_id": s.emp_id, "email": s.office_email,
                     "designation": s.designation, "department": s.department},
            "has_login": has_login(s), "username": s.username if has_login(s) else None,
        } for s in rows])


# --- extra records / plans (company admin) --------------------------------------

def purchase_json(p):
    return {"id": p.pk, "module_code": p.module.code, "module_name": p.module.name, "quantity": p.quantity,
            "unit_price": float(p.unit_price), "total_amount": float(p.total_amount), "status": p.status,
            "requested_by": p.requested_by, "created_at": p.created_at, "paid_at": p.paid_at}


class ExtraRecordListView(TenantView):
    permission_classes = [IsAuthenticated, IsCompanyAdmin]

    def get(self, request):
        qs = ExtraRecordPurchase.objects.filter(company=request.company).select_related("module")
        return Response([purchase_json(p) for p in qs])

    def post(self, request):
        cm = self.enabled_modules().get(request.data.get("module_code"))
        if not cm:
            raise ValidationError({"module_code": "Module not available for this company."})
        try:
            quantity = int(request.data.get("quantity"))
        except (TypeError, ValueError):
            quantity = 0
        if not 1 <= quantity <= 100000:
            raise ValidationError({"quantity": "Enter a number between 1 and 100000."})
        quota = module_quotas(request.company, counts={})[cm.module.code]
        if quota["limit"] is None:
            raise ValidationError({"detail": f"{cm.module.name} is unlimited on your plan."})
        purchase = request_extra_records(request.company, cm.module, quantity, requested_by=request.user.username)
        return Response(purchase_json(purchase), status=201)


class ExtraRecordCancelView(TenantView):
    permission_classes = [IsAuthenticated, IsCompanyAdmin]

    def post(self, request, pk):
        purchase = get_object_or_404(ExtraRecordPurchase, pk=pk, company=request.company)
        if purchase.status != ExtraRecordPurchase.PENDING:
            raise ValidationError({"detail": "Only pending requests can be cancelled."})
        purchase.status = ExtraRecordPurchase.CANCELLED
        purchase.save(update_fields=["status"])
        return Response(purchase_json(purchase))


def plan_request_json(r):
    return {"id": r.pk, "from_plan": r.from_plan.name if r.from_plan else None, "to_plan": r.to_plan.name,
            "to_plan_id": r.to_plan_id, "price": float(r.price), "status": r.status, "note": r.note,
            "requested_by": r.requested_by, "created_at": r.created_at, "decided_at": r.decided_at}


class PlanOptionsView(TenantView):
    permission_classes = [IsAuthenticated, IsCompanyAdmin]

    def get(self, request):
        module_ids = set(CompanyModule.objects.filter(company=request.company).values_list("module_id", flat=True))
        requests = PlanChangeRequest.objects.filter(company=request.company).select_related("from_plan", "to_plan")
        return Response({
            "current_plan_id": request.company.plan_id,
            "currency": CURRENCY,
            "plans": [plan_json(p, module_ids) for p in Plan.objects.prefetch_related("module_limits__module")],
            "requests": [plan_request_json(r) for r in requests[:20]],
            "can_request": True,
        })


class PlanRequestView(TenantView):
    permission_classes = [IsAuthenticated, IsCompanyAdmin]

    def post(self, request):
        plan = Plan.objects.filter(pk=request.data.get("plan")).first()
        if not plan:
            raise ValidationError({"plan": "Choose a plan."})
        if plan.pk == request.company.plan_id:
            raise ValidationError({"detail": f"You are already on the {plan.name} plan."})
        reason = (request.data.get("note") or "").strip()[:255]
        with transaction.atomic(using="default"):
            # One open request at a time (row lock while checking).
            from app.models.superadmin_masters.company import Company

            Company.objects.select_for_update().filter(pk=request.company.pk).first()
            if PlanChangeRequest.objects.filter(company=request.company, status=PlanChangeRequest.PENDING).exists():
                raise ValidationError({"detail": (
                    "You already have a pending plan request. Cancel it first to choose another plan.")})
            r = PlanChangeRequest.objects.create(
                company=request.company, from_plan=request.company.plan, to_plan=plan, price=plan.price,
                requested_by=request.user.username, note=reason)
        return Response(plan_request_json(r), status=201)


class PlanRequestCancelView(TenantView):
    permission_classes = [IsAuthenticated, IsCompanyAdmin]

    def post(self, request, pk):
        r = get_object_or_404(PlanChangeRequest, pk=pk, company=request.company)
        if r.status != PlanChangeRequest.PENDING:
            raise ValidationError({"detail": "Only pending requests can be cancelled."})
        r.status = PlanChangeRequest.CANCELLED
        r.decided_at = timezone.now()
        r.save(update_fields=["status", "decided_at"])
        return Response(plan_request_json(r))


# --- IWMS form settings and extra fields (any company login) ---------------------

class FormConfigView(TenantView):
    """Form settings for the IWMS screens: per form its routes, field
    overrides (label / required / hidden / order) and extra fields."""

    def get(self, request):
        from .iwms_forms import company_form_config

        return Response(company_form_config())


class ExtraValuesView(TenantView):
    """GET / PUT {data: {...}} the extra-field values of one IWMS record."""

    def extras(self, form):
        from .iwms_forms import company_form_config

        cfg = company_form_config().get(form)
        if not cfg or not cfg["extras"]:
            raise NotFound("This form has no extra fields.")
        return cfg["extras"]

    def get(self, request, form, record_id):
        from app.models.tenancy.tenant import ExtraFieldValue

        self.extras(form)
        row = ExtraFieldValue.objects.using(self.db).filter(form=form, record_id=record_id).first()
        return Response({"data": row.data if row else {}})

    def put(self, request, form, record_id):
        from app.models.tenancy.tenant import ExtraFieldValue

        data = clean_record_data(self.extras(form), request.data.get("data", {}))
        ExtraFieldValue.objects.using(self.db).update_or_create(
            form=form, record_id=str(record_id)[:100],
            defaults={"data": data, "updated_by_id": request.user.pk})
        return Response({"data": data})


# --- branding (company admin) ---------------------------------------------------

class CompanyLogoView(TenantView):
    permission_classes = [IsAuthenticated, IsCompanyAdmin]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        set_logo(request.company, request.FILES.get("logo"))
        return Response({"logo_url": logo_url(request.company)})

    def delete(self, request):
        remove_logo(request.company)
        return Response({"logo_url": None})


class CompanyBrandingView(TenantView):
    permission_classes = [IsAuthenticated, IsCompanyAdmin]

    def patch(self, request):
        try:
            request.company.theme_color = clean_theme_color(request.data.get("theme_color"))
        except ValidationError as e:
            raise ValidationError({"theme_color": e.detail})
        request.company.save(update_fields=["theme_color", "updated_at"])
        return Response({"theme_color": request.company.theme_color})

"""Super admin API: /api/super/..."""
from django.contrib.auth import authenticate
from django.contrib.auth.hashers import make_password
from django.db.models import ProtectedError, Sum
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from app.authentication.cookies import clear_auth_cookie, request_portal, set_auth_cookie
from app.models.superadmin.staff_management.staffcreation import Staffcreation
from app.models.superadmin_masters.company import Company
from app.models.superadmin_masters.project import Project
from app.models.tenancy.master import (
    CompanyModule, ExtraRecordPurchase, Module, Plan, PlanChangeRequest,
)

from .auth import SuperAdminAuthentication, super_token, tenant_token
from .billing import billable_users, decide_plan_request, user_room
from .companies import create_company, delete_company
from .context import tenant_context
from .data import ModuleData, data_tree
from .db import register_tenant_db
from .iwms_forms import company_forms, configured_forms, save_company_forms, save_module_forms
from .logos import logo_url, remove_logo, set_logo
from .permissions import company_permissions, save_company_permissions
from .module_tables import data_summary, sync_table
from .people import (
    check_not_reserved, check_password_rules, clean_email, clean_new_admins, create_admins, login_json,
    username_taken,
)
from .projects import admins_qs, delete_project, projects_json, save_project
from .serializers import (
    CompanySerializer, ExtraRecordPurchaseSerializer, ModuleSerializer, PlanChangeRequestSerializer,
    PlanSerializer, clean_field_definitions,
)
from .staff_access import refresh_staff_access


class SuperAdminMixin:
    authentication_classes = [SuperAdminAuthentication]
    permission_classes = [IsAuthenticated]


def user_json(user):
    return {"id": user.pk, "username": user.username, "email": user.email or "",
            "full_name": user.get_full_name() if hasattr(user, "get_full_name") else "",
            "last_login": user.last_login}


def change_password(user, data, save):
    """Shared by super admin and company profiles. Returns an error or None."""
    if not user.check_password(data.get("current_password") or ""):
        return "Current password is incorrect."
    new = data.get("new_password") or ""
    if len(new) < 6:
        return "New password must be at least 6 characters."
    if new == data.get("current_password"):
        return "New password must be different from the current one."
    user.set_password(new)
    save()
    return None


def _with_token(request, body, token, max_age):
    """Browsers get the token as an HttpOnly cookie; API clients in the body."""
    portal = request_portal(request)
    if portal is None:
        body["token"] = token
    response = Response(body)
    if portal:
        set_auth_cookie(response, portal, token, max_age=max_age)
    return response


class LoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        user = authenticate(username=(request.data.get("username") or "").strip(),
                            password=request.data.get("password") or "")
        if not user or not user.is_superuser or not user.is_active:
            return Response({"detail": "Invalid username or password."}, status=400)
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        token, max_age = super_token(user)
        return _with_token(request, {"user": user_json(user)}, token, max_age)


class LogoutView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        response = Response(status=204)
        portal = request_portal(request)
        if portal:
            clear_auth_cookie(response, portal)
        return response


class MeView(SuperAdminMixin, APIView):
    def get(self, request):
        return Response(user_json(request.user))

    def patch(self, request):
        user = request.user
        if "email" in request.data:
            user.email = clean_email(request.data.get("email"))
        if "full_name" in request.data and hasattr(user, "first_name"):
            first, _, last = (request.data.get("full_name") or "").strip().partition(" ")
            user.first_name, user.last_name = first[:150], last.strip()[:150]
        user.save()
        return Response(user_json(user))


class PasswordView(SuperAdminMixin, APIView):
    def post(self, request):
        user = request.user
        error = change_password(user, request.data, lambda: user.save(update_fields=["password"]))
        if error:
            return Response({"detail": error}, status=400)
        return Response(status=204)


class DashboardView(SuperAdminMixin, APIView):
    """Totals for the console's home page."""

    def get(self, request):
        companies = Company.objects.filter(is_deleted=False).exclude(db_name=None)
        users = records = 0
        errors = []
        for c in companies:
            try:
                alias = register_tenant_db(c.db_name)
                users += billable_users(alias).count()
            except Exception as exc:
                errors.append({"company": c.name, "error": str(exc)})
        tree = data_tree()
        records = sum(c["records"] for c in tree)
        paid = ExtraRecordPurchase.objects.filter(status=ExtraRecordPurchase.PAID).aggregate(t=Sum("total_amount"))
        monthly = sum(float(c.plan.price) for c in companies.select_related("plan") if c.plan and c.is_active)
        return Response({
            "companies": companies.count(), "active_companies": companies.filter(is_active=True).count(),
            "users": users, "records": records,
            "monthly_revenue": monthly, "extra_records_revenue": float(paid["t"] or 0),
            "pending_extra_records": ExtraRecordPurchase.objects.filter(status=ExtraRecordPurchase.PENDING).count(),
            "pending_plan_requests": PlanChangeRequest.objects.filter(status=PlanChangeRequest.PENDING).count(),
            "errors": errors,
        })


class PlanViewSet(SuperAdminMixin, viewsets.ModelViewSet):
    queryset = Plan.objects.prefetch_related("module_limits__module")
    serializer_class = PlanSerializer
    pagination_class = None
    filter_backends = []

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response({"detail": "Plan is in use by companies; move them to another plan first."},
                            status=400)


class ModuleViewSet(SuperAdminMixin, viewsets.ModelViewSet):
    queryset = Module.objects.all()
    serializer_class = ModuleSerializer
    pagination_class = None
    filter_backends = []

    @action(detail=False, methods=["get"], url_path="structure")
    def structure(self, request):
        """Module → sub-module → fields for a new company's projects."""
        from .iwms_tables import structure

        return Response({"modules": structure(None)})

    def perform_create(self, serializer):
        serializer.save(kind=Module.CUSTOM)

    def perform_update(self, serializer):
        module = serializer.save()
        errors = []
        for company in Company.objects.filter(is_deleted=False).exclude(db_name=None):
            try:
                alias = register_tenant_db(company.db_name)
                cm = CompanyModule.objects.filter(company=company, module=module).first()
                if cm and cm.fields is None and not module.is_builtin:
                    sync_table(alias, module.code, module.fields)
                refresh_staff_access(alias)  # module name copied into staff access
            except ValidationError:
                raise
            except Exception as exc:
                errors.append(f"{company.name}: {exc}")
        if errors:
            raise ValidationError({"detail": "Saved, but some companies could not be updated: " + "; ".join(errors)})

    @action(detail=True, methods=["get", "put"])
    def forms(self, request, pk=None):
        """Built-in module: its IWMS forms (screens), every field of each and
        the form settings for all companies. PUT {form: {fields, extras}}."""
        module = self.get_object()
        if not module.is_builtin:
            return Response({"forms": []})
        if request.method == "PUT":
            return Response({"forms": save_module_forms(module.code, request.data)})
        return Response({"forms": configured_forms(module.code)})

    def destroy(self, request, *args, **kwargs):
        module = self.get_object()
        if module.is_builtin:
            return Response({"detail": "Built-in modules can't be deleted; switch them off instead."}, status=400)
        if CompanyModule.objects.filter(module=module).exists():
            return Response({"detail": "Companies use this module. Remove it from their projects first."},
                            status=400)
        return super().destroy(request, *args, **kwargs)


class CompanyViewSet(SuperAdminMixin, viewsets.ModelViewSet):
    serializer_class = CompanySerializer
    pagination_class = None
    filter_backends = []
    lookup_value_regex = "[^/]+"

    def get_queryset(self):
        return (Company.objects.filter(is_deleted=False).exclude(db_name=None)
                .select_related("plan").prefetch_related("company_modules__module").order_by("name"))

    def perform_create(self, serializer):
        d = serializer.validated_data
        serializer.instance = create_company(
            name=d["name"], plan=d["plan"], contact_email=d.get("contact_email", ""),
            theme_color=d.get("theme_color", "#4f46e5"), admins=d["admins"], projects=d["projects"],
            module_fields=d.get("module_fields"),
        )

    def perform_destroy(self, instance):
        delete_company(instance)

    def _alias(self, company):
        return register_tenant_db(company.db_name)

    @action(detail=True, methods=["get", "post"])
    def projects(self, request, pk=None):
        company = self.get_object()
        if request.method == "POST":
            save_project(company, request.data)
            return Response(projects_json(company), status=201)
        return Response(projects_json(company))

    @action(detail=True, methods=["put", "delete"], url_path=r"projects/(?P<project_id>[^/]+)")
    def project_detail(self, request, pk=None, project_id=None):
        company = self.get_object()
        project = Project.objects.using(self._alias(company)).filter(pk=project_id, is_deleted=False).first()
        if not project:
            return Response({"detail": "Project not found."}, status=404)
        if request.method == "DELETE":
            delete_project(company, project)
        else:
            save_project(company, request.data, project)
        return Response(projects_json(company))

    @action(detail=True, methods=["put"], url_path="module-fields")
    def module_fields(self, request, pk=None):
        """{module_code: [field defs] | null} — the company's own fields."""
        company = self.get_object()
        data = request.data if isinstance(request.data, dict) else {}
        alias = self._alias(company)
        for code, fields in data.items():
            cm = CompanyModule.objects.select_related("module").filter(company=company, module__code=code).first()
            if not cm:
                continue
            if cm.module.is_builtin:
                if fields is not None and [f.get("name") for f in fields if isinstance(f, dict)] != \
                        [f["name"] for f in cm.module.fields]:
                    return Response({"fields": [
                        f"{cm.module.name} is a built-in IWMS module: its fields can't be changed per company."]},
                        status=400)
                continue
            try:
                cleaned = None if fields is None else clean_field_definitions(fields)
            except ValidationError as e:
                return Response({"fields": e.detail}, status=400)
            sync_table(alias, code, cleaned if cleaned is not None else cm.module.fields)
            cm.fields = cleaned
            cm.save(update_fields=["fields"])
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=["post", "delete"], parser_classes=[MultiPartParser, FormParser])
    def logo(self, request, pk=None):
        company = self.get_object()
        if request.method == "DELETE":
            remove_logo(company)
        else:
            set_logo(company, request.FILES.get("logo"))
        return Response({"logo_url": logo_url(company)})

    @action(detail=True, methods=["post"], url_path="open-workspace")
    def open_workspace(self, request, pk=None):
        """Open the company workspace as one of its admins. Body: {user_id?}."""
        company = self.get_object()
        if not company.is_active:
            return Response({"detail": "This company is disabled. Enable it first to open its workspace."},
                            status=400)
        admins = admins_qs(self._alias(company)).filter(login_enabled=True)
        user_id = request.data.get("user_id")
        admin = admins.filter(pk=user_id).first() if user_id else admins.order_by("created_at").first()
        if not admin:
            return Response({"detail": "This company has no active admin to view as."}, status=400)
        token, _ = tenant_token(admin, company, imp=request.user.username)
        return Response({"token": token, "slug": company.slug, "username": admin.username})

    @action(detail=True, methods=["get", "put"])
    def permissions(self, request, pk=None):
        """GET ?project=<id>: module → sub-module → actions + fields for one
        project. PUT {project, actions: {screen: [...]}, fields: {screen: [...] | null}}."""
        company = self.get_object()
        if request.method == "PUT":
            return Response(save_company_permissions(company, request.data))
        return Response(company_permissions(company, request.query_params.get("project")))

    @action(detail=True, methods=["get", "put"], url_path=r"iwms-fields/(?P<code>[^/]+)")
    def iwms_fields(self, request, pk=None, code=None):
        """GET: the module's forms with the fields this company uses.
        PUT {form: [enabled field names] | null (all)}: only those fields show
        in the company's IWMS screens."""
        company = self.get_object()
        module = Module.objects.filter(code=code, kind=Module.BUILTIN).first()
        if module is None:
            return Response({"detail": "Not a built-in IWMS module."}, status=404)
        if not CompanyModule.objects.filter(company=company, module=module).exists():
            return Response({"detail": f"{company.name} doesn't use {module.name} in any project."}, status=404)
        if request.method == "PUT":
            return Response({"forms": save_company_forms(company, code, request.data)})
        return Response({"forms": company_forms(company, code)})

    @action(detail=True, methods=["get"], url_path="structure")
    def structure(self, request, pk=None):
        """Database level for Edit project: module → sub-module → fields (?project=)."""
        from .iwms_tables import structure

        return Response({"modules": structure(self.get_object(), request.query_params.get("project") or None)})

    @action(detail=True, methods=["post"], url_path="structure-preview")
    def structure_preview(self, request, pk=None):
        """What saving {project?, modules: [codes], screens: {...}} would create/drop."""
        from .iwms_tables import clean_screens, preview

        data = request.data if isinstance(request.data, dict) else {}
        modules = [c for c in (data.get("modules") or []) if isinstance(c, str)]
        screens = clean_screens(data.get("screens")) or {}
        return Response(preview(self.get_object(), data.get("project") or None, modules, screens))

    @action(detail=True, methods=["get"], url_path="module-data")
    def module_data(self, request, pk=None):
        from .iwms_tables import table_rows

        alias = self._alias(self.get_object())
        data = data_summary(alias)
        for code, info in table_rows(alias).items():   # built-in modules' tables
            data.setdefault(code, {"rows": info["rows"], "columns": {}})["tables"] = info["tables"]
        return Response(data)

    @action(detail=True, methods=["get", "post"])
    def admins(self, request, pk=None):
        company = self.get_object()
        alias = self._alias(company)
        if request.method == "POST":
            cleaned = clean_new_admins([request.data], alias)
            user_room(company, alias, 1)
            create_admins(cleaned, alias, company)
        from app.models.tenancy.tenant import ProjectAdmin

        names = dict(Project.objects.using(alias).filter(is_deleted=False).values_list("pk", "name"))
        runs = {}
        for link in ProjectAdmin.objects.using(alias).all():
            if link.project_id in names:
                runs.setdefault(link.staff_id, []).append(names[link.project_id])
        people = Staffcreation.objects.using(alias).filter(is_company_admin=True, is_deleted=False)
        rows = [login_json(u, {"projects": ["All projects"] if u.is_developer else sorted(runs.get(u.pk, []))})
                for u in people.order_by("is_developer", "username")]
        return Response(rows, status=201 if request.method == "POST" else 200)

    @action(detail=True, methods=["post"], url_path="reset-admin-password")
    def reset_admin_password(self, request, pk=None):
        """Change a company admin's username and/or password (blank = keep)."""
        company = self.get_object()
        alias = self._alias(company)
        user = admins_qs(alias).filter(pk=request.data.get("user_id")).first()
        if not user:
            return Response({"detail": "Admin user not found."}, status=404)
        username = (request.data.get("username") or "").strip()
        password = request.data.get("password") or ""
        if not username:
            return Response({"detail": "Username is required."}, status=400)
        check_not_reserved(username)
        if username.lower() != (user.username or "").lower() and username_taken(alias, username, user.pk):
            return Response({"detail": f'Username "{username}" is already used in this company.'}, status=400)
        if password:
            check_password_rules(password)
        fields = ["username", "updated_by_id", "updated_at"]
        user.username, user.updated_by_id = username, None
        if password:
            user.password = make_password(password)
            fields.append("password")
        with tenant_context(alias):
            user.save(update_fields=fields)
        refresh_staff_access(alias)
        return Response(login_json(user))


class ExtraRecordPurchaseViewSet(SuperAdminMixin, viewsets.ModelViewSet):
    serializer_class = ExtraRecordPurchaseSerializer
    http_method_names = ["get", "post", "head", "options"]
    pagination_class = None
    filter_backends = []

    def get_queryset(self):
        qs = ExtraRecordPurchase.objects.select_related("company", "module")
        if self.request.query_params.get("status"):
            qs = qs.filter(status=self.request.query_params["status"])
        if self.request.query_params.get("company"):
            qs = qs.filter(company_id=self.request.query_params["company"])
        return qs

    def _set_status(self, new_status):
        purchase = self.get_object()
        if purchase.status != ExtraRecordPurchase.PENDING:
            return Response({"detail": f"This request is already {purchase.status}."}, status=400)
        purchase.status = new_status
        purchase.paid_at = timezone.now() if new_status == ExtraRecordPurchase.PAID else None
        purchase.save(update_fields=["status", "paid_at"])
        return Response(self.get_serializer(purchase).data)

    @action(detail=True, methods=["post"], url_path="mark-paid")
    def mark_paid(self, request, pk=None):
        return self._set_status(ExtraRecordPurchase.PAID)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self._set_status(ExtraRecordPurchase.CANCELLED)


class PlanChangeRequestViewSet(SuperAdminMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = PlanChangeRequestSerializer
    pagination_class = None
    filter_backends = []

    def get_queryset(self):
        qs = PlanChangeRequest.objects.select_related("company", "from_plan", "to_plan")
        if self.request.query_params.get("status"):
            qs = qs.filter(status=self.request.query_params["status"])
        return qs

    def _decide(self, approve):
        try:
            req = decide_plan_request(self.get_object(), approve, self.request.data.get("note", ""))
        except ValueError as e:
            return Response({"detail": str(e)}, status=400)
        return Response(self.get_serializer(req).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._decide(True)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self._decide(False)


class DataTreeView(SuperAdminMixin, APIView):
    def get(self, request):
        return Response(data_tree())


class DataRecordsView(SuperAdminMixin, APIView):
    parser_classes = [JSONParser]

    def target(self, company_id, project_id, module_code):
        company = Company.objects.filter(pk=company_id, is_deleted=False).select_related("plan").first()
        if not company:
            return None
        return ModuleData(company, project_id, module_code)

    def get(self, request, company_id, project_id, module_code):
        t = self.target(company_id, project_id, module_code)
        if not t:
            return Response({"detail": "Company not found."}, status=404)
        return Response({
            "company": {"id": t.company.pk, "name": t.company.name},
            "project": {"id": t.project.pk, "name": t.project.name},
            "module": {"code": t.code, "name": t.module.name, "kind": t.module.kind, "editable": t.editable},
            "fields": t.fields,
            "records": t.to_json(t.records(deleted=request.query_params.get("deleted") == "1")),
        })

    def post(self, request, company_id, project_id, module_code):
        t = self.target(company_id, project_id, module_code)
        if not t:
            return Response({"detail": "Company not found."}, status=404)
        row = t.create(request.data.get("data", {}), request.data.get("is_active", True))
        return Response(t.to_json([row])[0], status=201)


class DataRecordDetailView(DataRecordsView):
    def put(self, request, company_id, project_id, module_code, pk):
        t = self.target(company_id, project_id, module_code)
        if not t:
            return Response({"detail": "Company not found."}, status=404)
        row = t.update(pk, request.data.get("data", {}), request.data.get("is_active"))
        return Response(t.to_json([row])[0])

    def delete(self, request, company_id, project_id, module_code, pk):
        t = self.target(company_id, project_id, module_code)
        if not t:
            return Response({"detail": "Company not found."}, status=404)
        t.delete(pk)
        return Response(status=status.HTTP_204_NO_CONTENT)


class DataRecordRestoreView(DataRecordsView):
    def post(self, request, company_id, project_id, module_code, pk):
        t = self.target(company_id, project_id, module_code)
        if not t:
            return Response({"detail": "Company not found."}, status=404)
        row = t.restore(pk)
        return Response(t.to_json([row])[0])

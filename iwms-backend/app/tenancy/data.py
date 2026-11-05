"""Super admin access to every company's project data."""
import datetime
from decimal import Decimal

from django.db import models, transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from app.models.superadmin.staff_management.staffcreation import Staffcreation
from app.models.superadmin_masters.company import Company
from app.models.superadmin_masters.project import Project
from app.models.tenancy.master import CompanyModule, Module
from app.models.tenancy.tenant import ProjectModule

from .billing import module_quotas
from .context import tenant_context
from .db import register_tenant_db
from .logos import logo_url
from .module_tables import ModuleStore
from .modules import STAFF_MODULE, builtin_model, builtin_qs, ensure_lock_row, lock_and_count, project_counts
from .validation import clean_record_data

HIDDEN_COLUMNS = {"password", "previous_password", "fcm_token", "delete_reason"}
BROWSE_LIMIT = 500


def data_tree():
    """Every company -> projects -> modules, with record counts. A broken
    company DB is reported on that company and doesn't hide the others."""
    names = {m.code: m for m in Module.objects.all()}
    out = []
    for c in Company.objects.filter(is_deleted=False).exclude(db_name=None).select_related("plan").order_by("name"):
        item = {"id": c.pk, "name": c.name, "slug": c.slug, "logo_url": logo_url(c), "theme_color": c.theme_color,
                "is_active": c.is_active, "plan_name": c.plan.name if c.plan else None, "projects": [],
                "error": None}
        try:
            alias = register_tenant_db(c.db_name)
            used = [cm.module for cm in CompanyModule.objects.filter(company=c).select_related("module")]
            counts = project_counts(alias, used)
            pms = {}
            for pm in ProjectModule.objects.using(alias).all():
                pms.setdefault(pm.project_id, []).append(pm.module_code)
            for p in Project.objects.using(alias).filter(is_deleted=False).order_by("name"):
                mods = sorted(({"code": code, "name": names[code].name if code in names else code,
                                "kind": names[code].kind if code in names else "custom",
                                "records": counts.get(p.pk, {}).get(code, 0)} for code in pms.get(p.pk, [])),
                              key=lambda m: m["name"])
                item["projects"].append({"id": p.pk, "name": p.name, "is_active": p.is_active, "modules": mods,
                                         "records": sum(m["records"] for m in mods)})
        except Exception as exc:
            item["error"] = str(exc)
        item["records"] = sum(p["records"] for p in item["projects"])
        out.append(item)
    return out


def _field_type(field):
    if isinstance(field, (models.BooleanField,)):
        return "boolean"
    if isinstance(field, (models.DateTimeField,)):
        return "datetime"
    if isinstance(field, models.DateField):
        return "date"
    if isinstance(field, (models.IntegerField, models.FloatField, models.DecimalField)):
        return "number"
    if isinstance(field, models.TextField):
        return "textarea"
    return "text"


def _plain(value):
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, models.fields.files.FieldFile):
        return value.name or None
    if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
        return value
    return str(value)


class ModuleData:
    """One module of one project of one company."""

    def __init__(self, company, project_id, module_code):
        self.company = company
        self.alias = register_tenant_db(company.db_name)
        self.project = Project.objects.using(self.alias).filter(pk=project_id, is_deleted=False).first()
        if not self.project:
            raise NotFound("Project not found.")
        if not ProjectModule.objects.using(self.alias).filter(project=self.project, module_code=module_code).exists():
            raise NotFound("Module not in this project.")
        cm = CompanyModule.objects.select_related("module").filter(company=company, module__code=module_code).first()
        if not cm:
            raise NotFound("Module not available for this company.")
        self.code, self.module = module_code, cm.module
        if self.module.is_builtin and not self.module.model_label:
            self.model, self.fields, self.store = None, [], None   # no records (e.g. Dashboard)
        elif self.module.is_builtin:
            self.model = builtin_model(self.module)
            self.fields = [
                {"name": f.attname, "label": str(f.verbose_name).title(), "type": _field_type(f), "required": False}
                for f in self.model._meta.concrete_fields
                if f.attname not in HIDDEN_COLUMNS and not f.primary_key
            ][:40]
            self.store = None
        else:
            self.model = None
            self.fields = cm.effective_fields
            self.store = ModuleStore(self.alias, module_code, self.fields)

    @property
    def editable(self):
        return not self.module.is_builtin

    def records(self, deleted=False):
        if self.model is None and self.store is None:
            return []
        if self.store:
            qs = self.store.deleted_qs() if deleted else self.store.qs()
            return qs.filter(project_id=self.project.pk).order_by("-id")
        qs = self.model.objects.using(self.alias).filter(project_id=self.project.pk, is_deleted=deleted)
        if self.code == STAFF_MODULE:
            qs = qs.filter(is_developer=False)
        return qs.order_by("-pk")[:BROWSE_LIMIT]

    def get(self, pk, deleted=False):
        if self.model is None and self.store is None:
            row = None
        elif self.store:
            row = self.records(deleted).filter(pk=pk).first()
        else:
            row = self.model.objects.using(self.alias).filter(
                pk=pk, project_id=self.project.pk, is_deleted=deleted).first()
        if not row:
            raise NotFound("Record not found.")
        return row

    def to_json(self, rows):
        rows = list(rows)
        ids = {str(i) for r in rows for i in (r.created_by_id, r.updated_by_id, getattr(r, "deleted_by_id", None)) if i}
        names = dict(Staffcreation.objects.using(self.alias).filter(pk__in=ids).values_list("pk", "username"))
        who = lambda i: (names.get(str(i)) or str(i)) if i else "super admin"  # noqa: E731
        out = []
        for r in rows:
            if self.store:
                base = self.store.to_json(r)
            else:
                base = {"id": r.pk, "project_id": r.project_id,
                        "data": {f["name"]: _plain(getattr(r, f["name"], None)) for f in self.fields},
                        "is_active": r.is_active, "is_deleted": r.is_deleted,
                        "created_at": getattr(r, "created_at", None), "updated_at": getattr(r, "updated_at", None),
                        "deleted_at": getattr(r, "deleted_at", None)}
            out.append({**base, "created_by": who(r.created_by_id), "updated_by": who(r.updated_by_id),
                        "deleted_by": who(getattr(r, "deleted_by_id", None)) if r.is_deleted else None})
        return out

    def _room(self, message):
        quota = module_quotas(self.company, counts={}).get(self.code, {})
        limit = quota.get("limit")
        if limit is not None and lock_and_count(self.alias, self.module) >= limit:
            raise PermissionDenied(message)

    def _read_only(self):
        if not self.editable:
            raise ValidationError({"detail": (
                f"{self.module.name} is a built-in IWMS module: add or edit its records in the IWMS screens. "
                "Here you can view, delete and restore them.")})

    def create(self, data, is_active=True):
        self._read_only()
        data = clean_record_data(self.fields, data)
        ensure_lock_row(self.alias, self.code)
        with transaction.atomic(using=self.alias):
            if is_active:
                self._room(f"{self.module.name} is at the {self.company.plan.name} plan limit. "
                           "Add extra records or change the plan first.")
            return self.store.create(self.project.pk, data, is_active=bool(is_active))

    def update(self, pk, data, is_active=None):
        self._read_only()
        row = self.get(pk)
        data = clean_record_data(self.fields, data)
        ensure_lock_row(self.alias, self.code)
        with transaction.atomic(using=self.alias):
            if is_active is True and not row.is_active:
                self._room(f"{self.module.name} is at its plan limit, so this record cannot be made active.")
            return self.store.update(row, data, is_active=is_active)

    def delete(self, pk):
        row = self.get(pk)
        if self.store:
            self.store.soft_delete(row)
            return
        with tenant_context(self.alias):
            type(row).objects.using(self.alias).filter(pk=row.pk).update(
                is_deleted=True, deleted_at=timezone.now(), deleted_by_id=None)

    def restore(self, pk):
        row = self.get(pk, deleted=True)
        ensure_lock_row(self.alias, self.code)
        with transaction.atomic(using=self.alias):
            if row.is_active:
                self._room(f"{self.module.name} is at its plan limit, so this record cannot be restored.")
            if self.store:
                return self.store.restore(row)
            type(row).objects.using(self.alias).filter(pk=row.pk).update(
                is_deleted=False, deleted_at=None, deleted_by_id=None)
            row.refresh_from_db(using=self.alias)
            return row


def company_totals(company):
    """{module_code: counted rows} for the dashboard."""
    alias = register_tenant_db(company.db_name)
    mods = [cm.module for cm in CompanyModule.objects.filter(company=company).select_related("module")]
    return {m.code: builtin_qs(alias, m).count() if m.is_builtin and m.model_label else None for m in mods}

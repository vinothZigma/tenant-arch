import re

from rest_framework import serializers

from app.models.superadmin_masters.company import Company
from app.models.tenancy.master import (
    FIELD_TYPES, CompanyModule, ExtraRecordPurchase, Module, Plan, PlanChangeRequest, PlanModuleLimit,
)

from .billing import company_usage, request_extra_records
from .companies import admin_usernames
from .logos import clean_theme_color, logo_url
from .module_tables import SYSTEM_COLUMNS
from .projects import project_count


def clean_field_definitions(value):
    """[{"name": "email", "label": "Email", "type": "email", "required": true}, ...]"""
    if not isinstance(value, list):
        raise serializers.ValidationError("Must be a list of field definitions.")
    if not value:
        raise serializers.ValidationError("Add at least one field.")
    seen = set()
    for f in value:
        if not isinstance(f, dict):
            raise serializers.ValidationError("Each field must be an object.")
        name = f.get("name", "")
        if not isinstance(name, str) or not re.match(r"^[a-z][a-z0-9_]{0,62}$", name):
            raise serializers.ValidationError(
                f"Field name {name!r} must start with a letter and use only lowercase letters, digits or _.")
        if name in seen:
            raise serializers.ValidationError(f"Duplicate field name {name!r}.")
        if name in SYSTEM_COLUMNS:
            raise serializers.ValidationError(
                f"{name!r} is reserved (every module table already has it). Choose another name.")
        seen.add(name)
        if f.get("type", "text") not in FIELD_TYPES:
            raise serializers.ValidationError(f'Field {name!r}: type must be one of {", ".join(FIELD_TYPES)}.')
        f.setdefault("type", "text")
        f["label"] = (f.get("label") or name.replace("_", " ").title())[:100]
        f["required"] = bool(f.get("required", False))
    return value


class PlanModuleLimitSerializer(serializers.ModelSerializer):
    module = serializers.PrimaryKeyRelatedField(queryset=Module.objects.all())
    module_name = serializers.CharField(source="module.name", read_only=True)
    extra_record_price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)

    class Meta:
        model = PlanModuleLimit
        fields = ["module", "module_name", "record_limit", "extra_record_price"]


class PlanSerializer(serializers.ModelSerializer):
    company_count = serializers.SerializerMethodField()
    price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, required=False)
    module_limits = PlanModuleLimitSerializer(many=True, required=False)

    class Meta:
        model = Plan
        fields = ["id", "code", "name", "description", "price", "user_limit", "company_count", "module_limits"]

    def get_company_count(self, obj):
        return obj.companies.filter(is_deleted=False).count()

    def validate_module_limits(self, value):
        ids = [item["module"].id for item in value]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("Each module can only be listed once.")
        return value

    def _save_limits(self, plan, limits):
        if limits is None:
            return
        keep = []
        for item in limits:
            PlanModuleLimit.objects.update_or_create(plan=plan, module=item["module"], defaults={
                "record_limit": item["record_limit"], "extra_record_price": item["extra_record_price"]})
            keep.append(item["module"].id)
        plan.module_limits.exclude(module_id__in=keep).delete()

    def create(self, validated_data):
        limits = validated_data.pop("module_limits", [])
        plan = super().create(validated_data)
        self._save_limits(plan, limits)
        return plan

    def update(self, instance, validated_data):
        limits = validated_data.pop("module_limits", None)
        plan = super().update(instance, validated_data)
        self._save_limits(plan, limits)
        return plan


class ModuleSerializer(serializers.ModelSerializer):
    # Built-in modules: their IWMS sub-modules (catalog screens / forms).
    sub_modules = serializers.SerializerMethodField()

    class Meta:
        model = Module
        fields = ["id", "code", "name", "description", "kind", "model_label", "fields", "is_active", "sub_modules",
                  "core", "tables", "depends_on"]
        read_only_fields = ["kind", "model_label"]

    core = serializers.SerializerMethodField()
    tables = serializers.SerializerMethodField()
    depends_on = serializers.SerializerMethodField()

    def _graph(self, obj):
        from .iwms_tables import graph_json

        if obj.kind != Module.BUILTIN:
            return {"core": False, "tables": [f"module_{obj.code}"], "depends_on": []}
        return graph_json().get(obj.code, {"core": True, "tables": [], "depends_on": []})

    def get_core(self, obj):
        return self._graph(obj)["core"]

    def get_tables(self, obj):
        return self._graph(obj)["tables"]

    def get_depends_on(self, obj):
        return self._graph(obj)["depends_on"]

    def get_sub_modules(self, obj):
        if obj.kind != Module.BUILTIN:
            return []
        from app.utils import permission_catalog as catalog

        module = catalog.MODULES.get(obj.code) or {"screens": []}
        return [{"name": s["name"], "label": s["label"]} for s in module["screens"] if not s.get("superadmin_only")]

    def validate_fields(self, value):
        if self.instance is not None and self.instance.kind == Module.BUILTIN:
            names = [f.get("name") for f in value] if isinstance(value, list) else None
            if names != [f["name"] for f in self.instance.fields]:
                raise serializers.ValidationError(
                    f"{self.instance.name} is a built-in IWMS module: its fields are the columns of the IWMS "
                    "table and can't be changed here.")
            return self.instance.fields
        return clean_field_definitions(value)

    def validate_code(self, value):
        if self.instance and self.instance.code != value:
            raise serializers.ValidationError("Module code cannot be changed.")
        return value


class ExtraRecordPurchaseSerializer(serializers.ModelSerializer):
    company = serializers.PrimaryKeyRelatedField(queryset=Company.objects.filter(is_deleted=False))
    module = serializers.PrimaryKeyRelatedField(queryset=Module.objects.all())
    company_name = serializers.CharField(source="company.name", read_only=True)
    module_name = serializers.CharField(source="module.name", read_only=True)
    quantity = serializers.IntegerField(min_value=1, max_value=100000)
    unit_price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, required=False)
    status = serializers.ChoiceField(choices=ExtraRecordPurchase.STATUSES, default=ExtraRecordPurchase.PAID)

    class Meta:
        model = ExtraRecordPurchase
        fields = ["id", "company", "company_name", "module", "module_name", "quantity", "unit_price",
                  "total_amount", "status", "requested_by", "note", "created_at", "paid_at"]
        read_only_fields = ["total_amount", "requested_by", "created_at", "paid_at"]

    def create(self, d):
        """Super admin adding slots directly (e.g. paid offline)."""
        return request_extra_records(
            d["company"], d["module"], d["quantity"], requested_by="superadmin",
            status=d.get("status"), unit_price=d.get("unit_price"), note=d.get("note", ""))


class CompanyModuleSerializer(serializers.ModelSerializer):
    module_code = serializers.CharField(source="module.code", read_only=True)
    module_name = serializers.CharField(source="module.name", read_only=True)
    kind = serializers.CharField(source="module.kind", read_only=True)
    default_fields = serializers.JSONField(source="module.fields", read_only=True)

    class Meta:
        model = CompanyModule
        fields = ["module", "module_code", "module_name", "kind", "fields", "default_fields"]
        read_only_fields = fields


class CompanySerializer(serializers.ModelSerializer):
    """Read: company with usage. Create: the whole wizard. Update: name,
    contact email, colour, plan, active."""

    id = serializers.CharField(source="unique_id", read_only=True)
    plan = serializers.PrimaryKeyRelatedField(queryset=Plan.objects.all())
    modules = CompanyModuleSerializer(source="company_modules", many=True, read_only=True)
    plan_name = serializers.CharField(source="plan.name", read_only=True, default=None)
    usage = serializers.SerializerMethodField()
    admin_usernames = serializers.SerializerMethodField()
    project_count = serializers.SerializerMethodField()
    logo_url = serializers.SerializerMethodField()
    login_path = serializers.SerializerMethodField()
    error = serializers.SerializerMethodField()

    admins = serializers.ListField(child=serializers.DictField(), write_only=True, required=False)
    projects = serializers.ListField(child=serializers.DictField(), write_only=True, required=False)
    module_fields = serializers.DictField(write_only=True, required=False)

    class Meta:
        model = Company
        fields = [
            "id", "name", "slug", "db_name", "login_path", "plan", "plan_name", "modules",
            "contact_email", "logo_url", "theme_color", "is_active", "created_at", "usage",
            "admin_usernames", "project_count", "error", "admins", "projects", "module_fields",
        ]
        read_only_fields = ["slug", "db_name", "created_at"]

    def _safe(self, obj, fn, default=None):
        try:
            return fn(obj)
        except Exception as exc:  # one broken company DB must not break the list
            self._errors_by_company = getattr(self, "_errors_by_company", {})
            self._errors_by_company[obj.pk] = str(exc)
            return default

    def get_usage(self, obj):
        return self._safe(obj, company_usage)

    def get_admin_usernames(self, obj):
        return self._safe(obj, admin_usernames, [])

    def get_project_count(self, obj):
        return self._safe(obj, project_count)

    def get_error(self, obj):
        return getattr(self, "_errors_by_company", {}).get(obj.pk)

    def get_logo_url(self, obj):
        return logo_url(obj)

    def get_login_path(self, obj):
        return f"/{obj.slug}/login"

    def validate_theme_color(self, value):
        return clean_theme_color(value)

    def validate_name(self, value):
        value = value.strip()
        qs = Company.objects.filter(name__iexact=value, is_deleted=False)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(f'A company called "{value}" already exists.')
        return value

    def validate_module_fields(self, value):
        return {code: (None if f is None else clean_field_definitions(f)) for code, f in value.items()}

    def validate(self, attrs):
        if self.instance is None:
            for f, msg in (("admins", "Add at least one company admin."), ("projects", "Add at least one project.")):
                if not attrs.get(f):
                    raise serializers.ValidationError({f: msg})
        return attrs

    def update(self, instance, validated_data):
        for f in ("admins", "projects", "module_fields"):
            validated_data.pop(f, None)
        return super().update(instance, validated_data)


class PlanChangeRequestSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source="company.name", read_only=True)
    from_plan_name = serializers.CharField(source="from_plan.name", read_only=True, default=None)
    from_plan_price = serializers.DecimalField(source="from_plan.price", max_digits=10, decimal_places=2,
                                               read_only=True, default=None)
    to_plan_name = serializers.CharField(source="to_plan.name", read_only=True)

    class Meta:
        model = PlanChangeRequest
        fields = ["id", "company", "company_name", "from_plan", "from_plan_name", "from_plan_price",
                  "to_plan", "to_plan_name", "price", "status", "requested_by", "note", "created_at",
                  "decided_at"]
        read_only_fields = fields

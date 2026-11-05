"""Default plans and modules. Safe to run again.

Built-in modules are every module of the IWMS permission catalog
(app/utils/permission_catalog.py: Dashboard, Staff Management, Transport
Masters, Daily Operations, Complaint Management, Attendance, Reports, ...),
so plans, projects and permissions can switch each of them on. Modules whose
records are an IWMS table (MAIN_TABLES) can also be counted and limited.
"""
from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import models

from app.models.superadmin_masters.company import Company
from app.models.tenancy.master import Module, Plan, PlanModuleLimit
from app.utils import permission_catalog as catalog


def f(name, type="text", required=False, label=None):
    return {"name": name, "label": label or name.replace("_", " ").title(), "type": type, "required": required}


# Catalog module -> (IWMS model, main route whose POST creates a record, display columns or None = auto).
MAIN_TABLES = {
    "staff-creations": ("app.StaffcreationOfficeDetails", "staff-creations/staffcreation", [
        f("employee_name", label="Name"), f("emp_id", label="Emp ID"), f("designation"), f("department"),
        f("office_email", "email", label="Email"), f("doj", "date", label="Joined")]),
    "customers": ("app.CustomerCreation", "customer-masters/customercreations", [
        f("customer_name", label="Name"), f("customer_id", label="Customer ID"), f("contact_no", label="Phone"),
        f("street"), f("area")]),
    "transport-masters": ("app.VehicleCreation", "transport-masters/vehicle-creation", [
        f("vehicle_no", label="Vehicle No"), f("capacity", "number"), f("operational_status", label="Status"),
        f("insurance_expiry_date", "date", label="Insurance expiry")]),
    "waste-types": ("app.Bins", "waste-types/bins", [
        f("bin_name", label="Name"), f("bin_capacity", "number", label="Capacity"), f("bin_type", label="Type")]),
    "schedule-setup": ("app.Collection_point", "schedule-setup/collection-points", [
        f("cp_name", label="Name"), f("collection_type", label="Type"), f("latitude", "number"),
        f("longitude", "number")]),
    "schedule-operations": ("app.DailyTripAssignment", "schedule-operations/daily-trip-assignments", None),
    "complaint-ticket": ("app.ComplaintTicket", "complaint-ticket/tickets", None),
    "complaint-masters": ("app.ComplaintCategory", "complaint-masters/categories", None),
    "masters": ("app.Ward", "masters/wards", None),
    "role-assigns": ("app.StaffUserType", "role-assigns/staffusertypes", None),
    "attendance": ("app.AttendanceNew", "attendance/records", None),
}

# Built-in codes used before the catalog-based modules (renamed in place).
RENAMED = {"staff": "staff-creations", "vehicles": "transport-masters", "bins": "waste-types",
           "collection-points": "schedule-setup"}

# Custom modules (a real table per company): (code, name, description, fields)
CUSTOM = [
    ("assets", "Assets", "Equipment and tools register", [
        f("name", required=True), f("asset_tag", label="Asset tag"), f("purchased_on", "date"),
        f("cost", "number"), f("in_service", "boolean", label="In service"), f("notes", "textarea")]),
    ("vendors", "Vendors", "Suppliers and contractors", [
        f("name", required=True), f("email", "email"), f("phone"), f("gstin", label="GSTIN"),
        f("address", "textarea")]),
]

# (code, name, user limit, monthly price, description, {module: (record limit, price per extra record)})
PLANS = [
    ("basic", "Basic", 5, 999, "For a single small project", {
        "staff-creations": (25, 20), "customers": (500, 2), "transport-masters": (5, 200), "waste-types": (100, 10),
        "schedule-setup": (50, 10), "assets": (50, 20), "vendors": (20, 20)}),
    ("medium", "Medium", 25, 4999, "Several projects", {
        "staff-creations": (200, 15), "customers": (5000, 1), "transport-masters": (40, 150),
        "waste-types": (1000, 5), "schedule-setup": (500, 5), "assets": (500, 15), "vendors": (200, 15)}),
    ("high", "High", 100, 14999, "Large operations", {
        "staff-creations": (2000, 10), "customers": (50000, 1), "transport-masters": (400, 100),
        "waste-types": (10000, 2), "schedule-setup": (5000, 2), "assets": (5000, 10), "vendors": (2000, 10)}),
]

SKIP_COLUMNS = {"is_active", "is_deleted", "delete_reason", "created_by_id", "updated_by_id", "created_at",
                "updated_at", "deleted_at", "deleted_by_id", "company_id", "project_id", "password"}


def field_type(field):
    if isinstance(field, models.BooleanField):
        return "boolean"
    if isinstance(field, models.DateField) and not isinstance(field, models.DateTimeField):
        return "date"
    if isinstance(field, (models.IntegerField, models.FloatField, models.DecimalField)):
        return "number"
    if isinstance(field, models.EmailField):
        return "email"
    return "text"


def auto_columns(model_label, count=5):
    model = apps.get_model(*model_label.split(".", 1))
    cols = []
    for fld in model._meta.concrete_fields:
        if fld.primary_key or fld.attname in SKIP_COLUMNS or fld.attname.endswith(("_id", "_ids")):
            continue
        if isinstance(fld, (models.JSONField, models.FileField, models.BinaryField)):
            continue
        cols.append(f(fld.attname, field_type(fld), label=str(fld.verbose_name).title()))
        if len(cols) == count:
            break
    return cols


def catalog_routes():
    routes = {}
    for route, (module, _screen) in catalog.ROUTE_OWNERS.items():
        routes.setdefault(module, []).append(route.strip("/") + "/")
    return routes


class Command(BaseCommand):
    help = "Create the default plans (Basic/Medium/High) and modules (all IWMS modules + custom). Safe to re-run."

    def rename_old_builtin(self, old, new):
        """Move an earlier built-in code to its catalog code, everywhere."""
        from app.models.tenancy.tenant import ModuleLock, ModulePermission, ProjectModule
        from app.tenancy.db import register_tenant_db

        for company in Company.objects.filter(is_deleted=False).exclude(db_name=None):
            try:
                alias = register_tenant_db(company.db_name)
                for model in (ProjectModule, ModulePermission, ModuleLock):
                    model._base_manager.using(alias).filter(module_code=old).update(module_code=new)
            except Exception as exc:
                self.stderr.write(f"{company.name}: could not rename {old} -> {new}: {exc}")
        label = catalog.MODULES.get(new, {}).get("label")
        Module.objects.filter(code=old).update(code=new, **({"name": label} if label else {}))
        self.stdout.write(f"Module {old} renamed to {new}")

    def handle(self, *args, **options):
        for old, new in RENAMED.items():
            if Module.objects.filter(code=old, kind=Module.BUILTIN).exists() and not Module.objects.filter(code=new).exists():
                self.rename_old_builtin(old, new)

        routes = catalog_routes()
        for code, info in catalog.MODULES.items():
            screens = [s["label"] for s in info["screens"]]
            model_label, main_route, columns = MAIN_TABLES.get(code, ("", None, []))
            paths = routes.get(code, [])
            if main_route:
                main = main_route.strip("/") + "/"
                paths = [main] + [p for p in paths if p != main]
            if model_label and columns is None:
                columns = auto_columns(model_label)
            wiring = {"kind": Module.BUILTIN, "model_label": model_label, "api_paths": paths, "fields": columns or []}
            module, created = Module.objects.get_or_create(code=code, defaults={
                "name": info["label"], "description": ", ".join(screens)[:255], **wiring})
            if not created:
                # Keep the wiring in step with the code; name/description stay as edited.
                for key, value in wiring.items():
                    setattr(module, key, value)
                module.save(update_fields=list(wiring))
            self.stdout.write(f'Module {info["label"]} (IWMS): {"created" if created else "updated"}')

        for code, name, desc, fields in CUSTOM:
            _, created = Module.objects.get_or_create(code=code, defaults={
                "name": name, "description": desc, "kind": Module.CUSTOM, "fields": fields})
            self.stdout.write(f'Module {name}: {"created" if created else "exists"}')

        for code, name, users, price, desc, limits in PLANS:
            plan, created = Plan.objects.get_or_create(code=code, defaults={
                "name": name, "user_limit": users, "price": price, "description": desc})
            added = 0
            for module_code, (records, extra_price) in limits.items():
                module = Module.objects.filter(code=module_code).first()
                if module is None:
                    continue
                _, new = PlanModuleLimit.objects.get_or_create(
                    plan=plan, module=module, defaults={"record_limit": records, "extra_record_price": extra_price})
                added += new
            self.stdout.write(f'Plan {name}: {"created" if created else "exists"} ({added} limits added)')

"""Master-DB tables owned by the super admin. Nothing company-private here."""
from django.db import models

FIELD_TYPES = ["text", "textarea", "number", "email", "date", "boolean"]


class Plan(models.Model):
    """Subscription plan. Record limits are per module (PlanModuleLimit)."""

    code = models.SlugField(unique=True)            # basic / medium / high
    name = models.CharField(max_length=50)
    user_limit = models.PositiveIntegerField()      # users with login on (admins included)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # per month
    description = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tenancy_plan"
        ordering = ["price", "user_limit", "id"]

    def __str__(self):
        return self.name


class Module(models.Model):
    """A feature area a project can use and a plan can limit.

    kind="builtin": an existing IWMS feature (staff, vehicles, bins, ...).
        Its records are rows of `model_label` in the company DB, managed in
        the IWMS screens; `api_paths` are its /api/v1/ route prefixes (used
        to enforce project caps and plan limits on those routes).
    kind="custom": a module the super admin defines with `fields`; each
        company gets a real table module_<code> (app/tenancy/module_tables.py).
    """

    BUILTIN, CUSTOM = "builtin", "custom"
    KINDS = [(BUILTIN, "Built-in IWMS feature"), (CUSTOM, "Custom module")]

    code = models.SlugField(unique=True)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True, default="")
    kind = models.CharField(max_length=10, choices=KINDS, default=CUSTOM)
    model_label = models.CharField(max_length=100, blank=True, default="")
    api_paths = models.JSONField(default=list, blank=True)
    # [{"name": "email", "label": "Email", "type": "email", "required": true}]
    fields = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "tenancy_module"
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def is_builtin(self):
        return self.kind == self.BUILTIN


class CompanyModule(models.Model):
    """A module used by at least one of the company's projects, with the
    company's own form fields (null = the module's default fields)."""

    company = models.ForeignKey("app.Company", on_delete=models.CASCADE, related_name="company_modules")
    module = models.ForeignKey(Module, on_delete=models.CASCADE, related_name="company_modules")
    fields = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "tenancy_company_module"
        ordering = ["module__name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "module"], name="uniq_tenancy_company_module"),
        ]

    @property
    def effective_fields(self):
        # Built-in modules: `fields` are display columns of the IWMS model
        # (read-only here, records are managed in the IWMS screens).
        if self.module.is_builtin:
            return self.module.fields
        return self.fields if self.fields is not None else self.module.fields


class PlanModuleLimit(models.Model):
    """Records a plan includes for one module (all projects together) and the
    price of each extra one. No row = unlimited."""

    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name="module_limits")
    module = models.ForeignKey(Module, on_delete=models.CASCADE, related_name="plan_limits")
    record_limit = models.PositiveIntegerField()
    extra_record_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        db_table = "tenancy_plan_module_limit"
        ordering = ["module__name"]
        constraints = [
            models.UniqueConstraint(fields=["plan", "module"], name="uniq_tenancy_plan_module_limit"),
        ]


class ExtraRecordPurchase(models.Model):
    """Extra record slots bought for a module. Only paid slots count."""

    PENDING, PAID, CANCELLED = "pending", "paid", "cancelled"
    STATUSES = [(PENDING, "Pending payment"), (PAID, "Paid"), (CANCELLED, "Cancelled")]

    company = models.ForeignKey("app.Company", on_delete=models.CASCADE, related_name="extra_purchases")
    module = models.ForeignKey(Module, on_delete=models.CASCADE, related_name="extra_purchases")
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)    # snapshot at request time
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING)
    requested_by = models.CharField(max_length=150, blank=True, default="")
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tenancy_extra_record_purchase"
        ordering = ["-created_at", "-id"]


class PlanChangeRequest(models.Model):
    """A company asking to move to another plan; approving switches the plan."""

    PENDING, APPROVED, REJECTED, CANCELLED = "pending", "approved", "rejected", "cancelled"
    STATUSES = [(PENDING, "Pending"), (APPROVED, "Approved"), (REJECTED, "Rejected"), (CANCELLED, "Cancelled")]

    company = models.ForeignKey("app.Company", on_delete=models.CASCADE, related_name="plan_requests")
    from_plan = models.ForeignKey(Plan, on_delete=models.SET_NULL, null=True, related_name="+")
    to_plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name="change_requests")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING)
    requested_by = models.CharField(max_length=150, blank=True, default="")
    note = models.CharField(max_length=255, blank=True, default="")    # reason (e.g. on reject)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tenancy_plan_change_request"
        ordering = ["-created_at", "-id"]


class FormConfig(models.Model):
    """How a built-in IWMS form (catalog screen) looks for every company.

    fields: {field name: {"label": str, "required": bool, "hidden": bool, "order": int}}
            overrides of the IWMS model's own fields (missing keys = IWMS default);
    extras: [{"name", "label", "type", "required"}] new fields, stored per record
            in the company DB (tenancy ExtraFieldValue), shown under
            "Additional details" in the IWMS form.
    """

    module_code = models.SlugField()
    form = models.CharField(max_length=100)   # catalog screen name, e.g. "countries"
    fields = models.JSONField(default=dict, blank=True)
    extras = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenancy_form_config"
        constraints = [
            models.UniqueConstraint(fields=["module_code", "form"], name="uniq_tenancy_form_config"),
        ]


class PrintTemplate(models.Model):
    """A print layout for one report, designed by the super admin.

    layout: see app/tenancy/print_templates.py (DEFAULT_LAYOUT) — paper,
    header (logo / title / date), columns, totals, signatures, footer.
    Companies never edit it; the super admin assigns it to a company or one
    of its projects (PrintTemplateAssignment).
    """

    report_code = models.SlugField(max_length=50)    # e.g. "daily-trip-log"
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True, default="")
    layout = models.JSONField(default=dict, blank=True)
    # The template used by every company/project that has no assignment.
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenancy_print_template"
        ordering = ["report_code", "name", "id"]

    def __str__(self):
        return f"{self.report_code}: {self.name}"


class PrintTemplateAssignment(models.Model):
    """Which template a company (project_id="") or one project prints a report with.

    Lookup order: the project's row, then the company's row, then the
    report's default template. project_id is the company-DB Project pk
    (no ForeignKey across databases).
    """

    company = models.ForeignKey("app.Company", on_delete=models.CASCADE, related_name="print_templates")
    project_id = models.CharField(max_length=50, blank=True, default="")
    report_code = models.SlugField(max_length=50)
    template = models.ForeignKey(PrintTemplate, on_delete=models.CASCADE, related_name="assignments")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenancy_print_template_assignment"
        constraints = [
            models.UniqueConstraint(fields=["company", "project_id", "report_code"],
                                    name="uniq_tenancy_print_template_assignment"),
        ]


class ExcelColumnSetting(models.Model):
    """Which fields a company (project_id="") or one project gets in Excel.

    kind "export": the columns (and order/headers) of Excel downloads.
    kind "import": the columns of the upload template, and the only ones an
    upload may fill. columns = [{"field", "header"}] in order; the fields are
    checked against app/tenancy/excel_columns.py (screen_fields).
    Lookup: the project's row, then the company's row, then every field.
    """

    EXPORT, IMPORT = "export", "import"
    KINDS = ((EXPORT, "Export"), (IMPORT, "Import"))

    company = models.ForeignKey("app.Company", on_delete=models.CASCADE, related_name="excel_columns")
    project_id = models.CharField(max_length=50, blank=True, default="")
    screen = models.CharField(max_length=100)      # IWMS userscreen name, e.g. "customercreations"
    kind = models.CharField(max_length=10, choices=KINDS)
    columns = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenancy_excel_column_setting"
        constraints = [
            models.UniqueConstraint(fields=["company", "project_id", "screen", "kind"],
                                    name="uniq_tenancy_excel_column_setting"),
        ]

    def __str__(self):
        return f"{self.company_id}/{self.project_id or '*'} {self.screen} {self.kind}"

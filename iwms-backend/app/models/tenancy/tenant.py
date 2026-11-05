"""Company-DB tables of the project/module/permission system.

Modules are referenced by `module_code`, not a ForeignKey: the Module table
lives in the master DB. New code reads these with `.using(alias)`.
"""
from django.db import models

from app.utils.base_models import BaseMaster


class LiveManager(models.Manager):
    """Hides soft-deleted rows. Use `all_objects` to include them."""

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class ProjectModule(BaseMaster):
    """A module in a project with the most anyone may do in it (set by the
    super admin; view is always allowed). The project's admins get exactly
    this, their staff the same or less."""

    id = models.BigAutoField(primary_key=True)
    project = models.ForeignKey("app.Project", on_delete=models.CASCADE, related_name="plan_modules")
    module_code = models.SlugField()
    can_create = models.BooleanField(default=True)
    can_edit = models.BooleanField(default=True)
    can_delete = models.BooleanField(default=True)

    objects = LiveManager()
    all_objects = models.Manager()

    class Meta:
        db_table = "tenancy_project_module"
        constraints = [
            models.UniqueConstraint(fields=["project", "module_code"], name="uniq_tenancy_project_module"),
        ]

    @property
    def permissions(self):
        return {"view": True, "create": self.can_create, "edit": self.can_edit, "delete": self.can_delete}

    def delete(self, *args, **kwargs):  # never cascade through BaseMaster here
        return models.Model.delete(self, *args, **kwargs)


class ProjectAdmin(models.Model):
    """Which company admins run which project (assigned by the super admin)."""

    project = models.ForeignKey("app.Project", on_delete=models.CASCADE, related_name="admin_links")
    staff = models.ForeignKey(
        "app.StaffcreationOfficeDetails", on_delete=models.CASCADE, related_name="admin_project_links"
    )

    class Meta:
        db_table = "tenancy_project_admin"
        constraints = [
            models.UniqueConstraint(fields=["project", "staff"], name="uniq_tenancy_project_admin"),
        ]


class ModulePermission(BaseMaster):
    """Staff access configuration: what one staff login may do in one module
    of one project. Only rows that grant access exist.

    username / staff_name / login_enabled / project_name / module_name /
    access are readable copies kept in sync by app/tenancy/staff_access.py.
    """

    id = models.BigAutoField(primary_key=True)
    staff = models.ForeignKey(
        "app.StaffcreationOfficeDetails", on_delete=models.CASCADE, related_name="module_permissions"
    )
    username = models.CharField(max_length=150, blank=True, default="")
    staff_name = models.CharField(max_length=200, blank=True, default="")
    login_enabled = models.BooleanField(default=True)
    project = models.ForeignKey("app.Project", on_delete=models.CASCADE, related_name="module_permissions")
    project_name = models.CharField(max_length=150, blank=True, default="")
    module_code = models.SlugField()
    module_name = models.CharField(max_length=100, blank=True, default="")
    can_view = models.BooleanField(default=False)
    can_create = models.BooleanField(default=False)
    can_edit = models.BooleanField(default=False)
    can_delete = models.BooleanField(default=False)
    access = models.CharField(max_length=40, blank=True, default="")  # "View, Create" ...

    objects = LiveManager()
    all_objects = models.Manager()

    class Meta:
        db_table = "staff_access_configuration"
        constraints = [
            models.UniqueConstraint(
                fields=["staff", "project", "module_code"], name="uniq_tenancy_staff_project_module"
            ),
        ]

    def delete(self, *args, **kwargs):
        return models.Model.delete(self, *args, **kwargs)


class ModuleTable(models.Model):
    """A module_<code> table that exists in this company DB."""

    module_code = models.SlugField(unique=True)
    system_version = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tenancy_module_table"


class ModuleColumn(models.Model):
    """A field column of a module table."""

    table = models.ForeignKey(ModuleTable, on_delete=models.CASCADE, related_name="columns")
    name = models.CharField(max_length=64)
    type = models.CharField(max_length=20)
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "tenancy_module_column"
        constraints = [models.UniqueConstraint(fields=["table", "name"], name="uniq_tenancy_module_column")]


class ModuleLock(models.Model):
    """One row per module code. Creates that must respect a plan limit first
    UPDATE this row inside their transaction: on MySQL/MariaDB that is a row
    lock, on SQLite a database write lock, so two parallel creates can never
    both pass the limit (see app/tenancy/modules.py: lock_and_count)."""

    module_code = models.SlugField(unique=True)
    counter = models.BigIntegerField(default=0)

    class Meta:
        db_table = "tenancy_module_lock"


class ExtraFieldValue(models.Model):
    """Values of a form's extra fields (FormConfig.extras) for one record."""

    form = models.CharField(max_length=100)        # catalog screen name
    record_id = models.CharField(max_length=100)   # the IWMS record's primary key
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by_id = models.CharField(max_length=50, null=True, blank=True)

    class Meta:
        db_table = "tenancy_extra_field_value"
        constraints = [
            models.UniqueConstraint(fields=["form", "record_id"], name="uniq_tenancy_extra_field_value"),
        ]

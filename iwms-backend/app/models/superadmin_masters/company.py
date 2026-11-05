from django.db import models

from app.utils.base_models import BaseMaster
from app.utils.comfun import generate_unique_id


def generate_company_id():
    return f"CMP-{generate_unique_id()}"


class Company(BaseMaster):
    unique_id = models.CharField(
        max_length=30,
        primary_key=True,
        unique=True,
        default=generate_company_id,
    )

    name = models.CharField(max_length=150)
    description = models.TextField(blank=True, null=True)

    # Multi-tenancy (app/tenancy/). Filled once by save() from the name and
    # never changed afterwards: the slug is the company's URL segment and
    # db_name its own database ("Tata Motors" -> tata-motors ->
    # iwms_company_tata_motors). Not editable through the API.
    slug = models.SlugField(max_length=40, unique=True, null=True, blank=True, editable=False)
    db_name = models.CharField(max_length=64, unique=True, null=True, blank=True, editable=False)

    # Plan, contact and branding (set by the super admin / company admin).
    plan = models.ForeignKey(
        "app.Plan", on_delete=models.PROTECT, null=True, blank=True, related_name="companies"
    )
    contact_email = models.EmailField(blank=True, default="")
    theme_color = models.CharField(max_length=7, default="#4f46e5")  # '#rrggbb'
    company_logo = models.ImageField(
        upload_to="company_logos/",
        blank=True,
        null=True,
    )

    CASCADE_SOFT_DELETE = (
        "projects",
        "district_set",
        "plants",
        "departments",
        "designations",
        "staff_office_details",
        "staff_personal_details",
        "staff_templates",
        "staffusertype_set",
        "contractorusertype_set",
        "usertype_set",
        "wastetype_set",
        "mainscreentype_set",
        "mainscreen_set",
        "userscreen_set",
        "userscreenaction_set",
        "userscreen_column_permissions",
        "user_set",
        "maincategory_set",
        "complaint_set",
        "complaint_categories",
        "complaint_subcategories",
        "complaint_sla_rules",
        "staff_access_configurations",
        "customer_access_configurations",
        "property_set",
        "subproperty_set",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        from app.tenancy.slugs import db_name_for_slug, make_slug

        filled = []
        if not self.slug:
            self.slug = make_slug(self.name, exclude_pk=self.pk)
            filled.append("slug")
        if not self.db_name:
            self.db_name = db_name_for_slug(self.slug)
            filled.append("db_name")
        update_fields = kwargs.get("update_fields")
        if filled and update_fields is not None:
            kwargs["update_fields"] = [*update_fields, *(f for f in filled if f not in update_fields)]
        super().save(*args, **kwargs)

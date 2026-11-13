"""Sub-modules (forms) of the built-in IWMS modules, for plan limits.

A built-in module (e.g. Transport Masters) has forms: Vehicle Type, Vehicle
Creation, Fuel (FORMS below). Plans set a record limit per form
(PlanScreenLimit, keyed by the form's main catalog screen); this file says
which model each form's records live in and how they are counted.

Counted rows: not deleted and active (like module limits, app/tenancy/modules.py),
across all projects of the company. Staff Creation leaves out admin and
developer logins (those count against the user limit instead).
"""
from functools import lru_cache

from django.utils.text import slugify

from . import iwms_tables as T

STAFF_SCREEN = "staffcreation"


def _field_names(model):
    return {f.name for f in model._meta.get_fields() if getattr(f, "concrete", False)}


@lru_cache(maxsize=None)
def screen_model(screen):
    """The model of the sub-module's records (its first table), or None."""
    tables = T.SCREEN_TABLES.get(screen) or []
    return T._model(tables[0]) if tables else None


# Plans are set per FORM, as the IWMS screens show them (each entry is a page
# with a "New" form in iwms-frontend/src/layouts/admin/encryptedRouting/
# adminRoutes.ts). A form = its main catalog screen, whose records are counted
# (one record per save). Some forms save into several tables or cover several
# screens: those are listed as `parts` and are NOT counted separately — e.g. one
# Daily Trip Plan = 1 trip assignment with its trip collection points and
# household collection points.
# Audits, reports, dashboards, trackers, logs and list-only pages have no form
# and get no limit. Keep in step with the frontend when forms are added.
FORMS = {
    "role-assigns": [
        ("user-type", None, ()),
        ("staffusertypes", "Staff User Type", ("contractorusertypes",)),
        ("project-staff-hierarchy", None, ()),
    ],
    "staff-creations": [
        ("department-masters", None, ()),
        ("designation-masters", None, ()),
        ("staffcreation", None, ("Personal details",)),
        ("staff-access-configuration", None, ("Screen permissions",)),
    ],
    "common-masters": [("continents", None, ()), ("countries", None, ()), ("states", None, ())],
    "complaint-masters": [
        ("types", "Complaint Types", ()),
        ("categories", "Complaint Categories", ()),
        ("subcategories", "Complaint Subcategories", ()),
        ("sla-rules", None, ("Escalation levels", "Routing rules")),
    ],
    "masters": [
        ("districts", None, ()), ("cities", None, ()), ("zones", None, ()), ("wards", None, ()),
        ("panchayat", None, ()), ("panchayat-leaders", None, ()), ("district-leaders", None, ()),
        ("plants", None, ()),
    ],
    "waste-types": [("properties", None, ()), ("subproperties", None, ()), ("bins", None, ()), ("waste type", None, ())],
    "transport-masters": [
        ("vehicle-type", None, ()),
        ("vehicle-creation", None, ("Document history",)),
        ("fuels", None, ()),
    ],
    "customers": [("customercreations", None, ())],
    "schedule-setup": [
        ("staff-templates", None, ()),
        ("alternative-staff-templates", None, ()),
        ("collection-points", None, ()),
        ("trip-plans", None, ("Collection points", "Vehicle substitutions")),
    ],
    "schedule-operations": [
        ("daily-trip-assignments", "Daily Trip Plan",
         ("daily-trip-collection-points", "daily-trip-household-collections")),
        ("bin-collection-events", None, ()),
        ("wastecollections", None, ()),
        ("vehicle-breakdowns", None, ("Photos",)),
        ("trip-delay-reports", None, ()),
        ("retrip-requests", None, ()),
    ],
    "complaint-ticket": [("tickets", None, ("Comments", "Attachments", "History"))],
}


def _catalog_label(screen):
    return T.catalog.SCREEN_LABELS.get(screen, screen)


@lru_cache(maxsize=None)
def module_sub_modules(code):
    """The module's FORMS, in sidebar order: [{"screen" (main screen, counted),
    "label", "parts" (what the same form also saves, not counted)}]."""
    out = []
    for screen, label, parts in FORMS.get(code, ()):
        if screen_model(screen) is None:
            continue
        out.append({
            "screen": screen,
            "label": label or _catalog_label(screen),
            # a part is another catalog screen (shown by its label) or plain text
            "parts": tuple(_catalog_label(p) if p in T.catalog.SCREEN_LABELS else p for p in parts),
        })
    return tuple(out)


def screen_label(screen):
    code = T.SCREEN_MODULE.get(screen)
    for s in module_sub_modules(code) if code else ():
        if s["screen"] == screen:
            return s["label"]
    return screen


def screen_parts(screen):
    code = T.SCREEN_MODULE.get(screen)
    for s in module_sub_modules(code) if code else ():
        if s["screen"] == screen:
            return s["parts"]
    return ()


def screen_module_code(screen):
    return T.SCREEN_MODULE.get(screen)


@lru_cache(maxsize=None)
def screens_for_model(label):
    """Sub-modules whose records are rows of `label` ("app.vehiclecreation")."""
    label = label.lower()
    return tuple(
        s for s in T.SCREEN_MODULE
        if (m := screen_model(s)) is not None and m._meta.label_lower == label
    )


def primary_screen(module):
    """The sub-module a built-in module's old (module-level) limit counted."""
    if not module.model_label:
        return None
    for s in module_sub_modules(module.code):
        m = screen_model(s["screen"])
        if m is not None and m._meta.label_lower == module.model_label.lower():
            return s["screen"]
    return None


def screen_qs(alias, screen, counted=True):
    model = screen_model(screen)
    qs = model.objects.using(alias).all()
    names = _field_names(model)
    if "is_deleted" in names:
        qs = qs.filter(is_deleted=False)
    if counted and "is_active" in names:
        qs = qs.filter(is_active=True)
    if screen == STAFF_SCREEN:
        qs = qs.filter(is_company_admin=False, is_developer=False)
    return qs


def is_counted(screen, instance):
    """Would saving `instance` add a counted row to this sub-module?"""
    if getattr(instance, "is_deleted", False) or getattr(instance, "is_active", True) is False:
        return False
    if screen == STAFF_SCREEN and (getattr(instance, "is_company_admin", False)
                                   or getattr(instance, "is_developer", False)):
        return False
    return True


def lock_key(screen):
    """ModuleLock row of a sub-module (ModuleLock.module_code is a slug)."""
    return f"screen-{slugify(screen)}"[:50]

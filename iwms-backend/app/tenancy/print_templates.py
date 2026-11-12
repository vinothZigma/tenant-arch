"""Print templates: the super admin designs how a report prints, then assigns
a design to a company or to one of its projects.

* REPORTS lists the printable reports and the columns each one can show.
  Adding a report = one entry here + a Print button that calls
  printReport() (frontend src/utils/printTemplate.ts) with rows keyed by
  these column keys.
* A template's `layout` is cleaned by clean_layout() (unknown keys dropped,
  values checked), so the frontend renderer can trust its shape.
* resolve_template(): project assignment -> company assignment -> the
  report's default template -> None (the frontend then uses DEFAULT_LAYOUT).
"""
from copy import deepcopy

from rest_framework.exceptions import ValidationError

# ---------------------------------------------------------------- reports

REPORTS = {
    "daily-trip-log": {
        "name": "Daily Trip Log",
        "title": "Daily Trip Log Report",
        "columns": [
            {"key": "trip_id", "label": "Trip ID"},
            {"key": "trip_assignment", "label": "Trip Assignment"},
            {"key": "trip_date", "label": "Trip Date"},
            {"key": "location", "label": "Location"},
            {"key": "zone", "label": "Zone"},
            {"key": "driver", "label": "Driver"},
            {"key": "operator", "label": "Operator"},
            {"key": "vehicle", "label": "Vehicle"},
            {"key": "log_status", "label": "Log Status"},
            {"key": "collection_status", "label": "Collection Status"},
            {"key": "collection_time", "label": "Collection Time"},
            {"key": "point_type", "label": "Point Type"},
            {"key": "point", "label": "Collection Point / Customer"},
            {"key": "waste_type", "label": "Waste Type"},
            {"key": "weight_kg", "label": "Collected Weight (kg)", "numeric": True},
            {"key": "is_collected", "label": "Is Collected"},
        ],
    },
}


def report_json(code):
    report = REPORTS[code]
    return {"code": code, "name": report["name"], "title": report["title"], "columns": report["columns"]}


def reports_json():
    return [report_json(code) for code in REPORTS]


def check_report(code):
    if code not in REPORTS:
        raise ValidationError({"report_code": f"Unknown report: {code!r}."})
    return code


# ---------------------------------------------------------------- layout

POSITIONS = ("left", "center", "right")
SIGNATURE_LAYOUTS = ("none", "left", "center", "right", "left-right", "three")
TEXT_LIMIT = 300

DEFAULT_LAYOUT = {
    "paper": "A4",                 # A4 | A3 | Letter | Legal
    "orientation": "landscape",    # portrait | landscape
    "font_size": 10,               # pt, 7..14
    "accent_color": "#1e40af",     # header band / table head
    # header
    "logo": "left",                # left | center | right | none
    "title": "",                   # "" = the report's own title
    "title_align": "center",
    "subtitle": "{company} · {project}",
    "show_date": True,
    "date_position": "right",
    "header_note": "",             # free text under the title, placeholders allowed
    "show_filters": True,          # "Filters: Trip date 05-11-2026 · Verified"
    # table
    "columns": [],                 # [{key, label?}]; [] = every column of the report
    "show_serial": True,
    "striped": True,
    "show_totals": True,           # sums numeric columns in a last row
    # signatures
    "signature_layout": "right",
    "signatures": ["Authorised Signatory"],   # labels, in layout order
    # footer
    "footer_text": "Generated from IWMS",
    "show_page_numbers": True,
    "show_printed_by": True,
}

SAMPLE_TEMPLATES = [
    {
        "name": "Classic – sign right",
        "description": "Logo left, centred title, date right, one signature on the right.",
        "is_default": True,
        "layout": {},
    },
    {
        "name": "Corporate – sign left",
        "description": "Logo right, title left, no date, signature on the left.",
        "layout": {
            "logo": "right", "title_align": "left", "show_date": False,
            "accent_color": "#047857", "signature_layout": "left", "signatures": ["Commissioner"],
        },
    },
    {
        "name": "Two signatures",
        "description": "Centred logo, compact columns, Prepared by / Approved by.",
        "layout": {
            "logo": "center", "title_align": "center", "orientation": "portrait",
            "accent_color": "#7c3aed", "signature_layout": "left-right",
            "signatures": ["Prepared by", "Approved by"],
            "columns": [{"key": k} for k in (
                "trip_date", "vehicle", "driver", "location", "point", "weight_kg", "log_status")],
        },
    },
    {
        "name": "Minimal",
        "description": "No logo, no stripes, centred signature, few columns.",
        "layout": {
            "logo": "none", "title_align": "center", "striped": False, "show_filters": False,
            "accent_color": "#334155", "orientation": "portrait", "signature_layout": "center",
            "signatures": ["Supervisor"], "show_printed_by": False, "footer_text": "",
            "columns": [{"key": k} for k in ("trip_date", "vehicle", "point", "weight_kg")],
        },
    },
]


def _text(value, field, limit=TEXT_LIMIT):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationError({field: "Must be text."})
    return value.strip()[:limit]


def _choice(value, field, choices):
    if value not in choices:
        raise ValidationError({field: f"Choose one of: {', '.join(choices)}."})
    return value


def _bool(value, field):
    if not isinstance(value, bool):
        raise ValidationError({field: "Must be true or false."})
    return value


def clean_layout(report_code, raw):
    """A full, valid layout from a partial one (missing keys = defaults)."""
    from .logos import HEX_COLOR

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValidationError({"layout": "Must be an object."})
    out = deepcopy(DEFAULT_LAYOUT)
    data = {k: v for k, v in raw.items() if k in DEFAULT_LAYOUT}

    if "paper" in data:
        out["paper"] = _choice(data["paper"], "paper", ("A4", "A3", "Letter", "Legal"))
    if "orientation" in data:
        out["orientation"] = _choice(data["orientation"], "orientation", ("portrait", "landscape"))
    if "font_size" in data:
        size = data["font_size"]
        if not isinstance(size, (int, float)) or isinstance(size, bool) or not 7 <= size <= 14:
            raise ValidationError({"font_size": "Use a size from 7 to 14."})
        out["font_size"] = round(float(size), 1)
    if "accent_color" in data:
        color = _text(data["accent_color"], "accent_color", 7)
        if not HEX_COLOR.match(color):
            raise ValidationError({"accent_color": "Use a colour like #1e40af."})
        out["accent_color"] = color.lower()
    if "logo" in data:
        out["logo"] = _choice(data["logo"], "logo", POSITIONS + ("none",))
    for key in ("title_align", "date_position"):
        if key in data:
            out[key] = _choice(data[key], key, POSITIONS)
    for key in ("title", "subtitle", "header_note", "footer_text"):
        if key in data:
            out[key] = _text(data[key], key)
    for key in ("show_date", "show_filters", "show_serial", "striped", "show_totals",
                "show_page_numbers", "show_printed_by"):
        if key in data:
            out[key] = _bool(data[key], key)
    if "signature_layout" in data:
        out["signature_layout"] = _choice(data["signature_layout"], "signature_layout", SIGNATURE_LAYOUTS)
    if "signatures" in data:
        sigs = data["signatures"]
        if not isinstance(sigs, list) or len(sigs) > 3:
            raise ValidationError({"signatures": "Up to 3 signature labels."})
        out["signatures"] = [_text(s, "signatures", 60) for s in sigs]
    if "columns" in data:
        out["columns"] = _clean_columns(report_code, data["columns"])

    # The signature count follows the layout.
    wanted = {"none": 0, "left-right": 2, "three": 3}.get(out["signature_layout"], 1)
    sigs = (out["signatures"] + ["Signature"] * wanted)[:wanted]
    out["signatures"] = [s or "Signature" for s in sigs]
    return out


def _clean_columns(report_code, raw):
    if not isinstance(raw, list):
        raise ValidationError({"columns": "Must be a list."})
    known = {c["key"] for c in REPORTS[report_code]["columns"]}
    columns, seen = [], set()
    for item in raw:
        if not isinstance(item, dict) or item.get("key") not in known:
            raise ValidationError({"columns": f"Unknown column: {item!r}."})
        if item["key"] in seen:
            continue
        seen.add(item["key"])
        column = {"key": item["key"]}
        label = _text(item.get("label"), "columns", 60)
        if label:
            column["label"] = label
        columns.append(column)
    return columns


# ---------------------------------------------------------------- lookup

def template_json(template):
    return {
        "id": template.pk, "report_code": template.report_code, "name": template.name,
        "description": template.description, "is_default": template.is_default,
        "layout": clean_layout(template.report_code, template.layout),
        "updated_at": template.updated_at,
    }


def resolve_template(company, project_id, report_code):
    """(template, source): source = "project" | "company" | "default" | None."""
    from app.models.tenancy.master import PrintTemplate, PrintTemplateAssignment

    if company is not None:
        rows = {
            row.project_id: row
            for row in PrintTemplateAssignment.objects.select_related("template").filter(
                company=company, report_code=report_code, project_id__in=[str(project_id or ""), ""])
        }
        if project_id and str(project_id) in rows:
            return rows[str(project_id)].template, "project"
        if "" in rows:
            return rows[""].template, "company"
    default = PrintTemplate.objects.filter(report_code=report_code, is_default=True).first()
    return (default, "default") if default else (None, None)


def ensure_sample_templates():
    """Create the sample templates of every report that has none (idempotent)."""
    from app.models.tenancy.master import PrintTemplate

    created = 0
    for code in REPORTS:
        if PrintTemplate.objects.filter(report_code=code).exists():
            continue
        for sample in SAMPLE_TEMPLATES:
            PrintTemplate.objects.create(
                report_code=code, name=sample["name"], description=sample["description"],
                is_default=sample.get("is_default", False), layout=clean_layout(code, sample["layout"]),
            )
            created += 1
    return created

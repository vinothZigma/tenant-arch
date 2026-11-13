"""Project part of the IWMS URL: /<company>/<project>/login, /<company>/<project>/admin ...

"Palakkad BP" -> "palakkad-bp". Must give exactly the same slugs as
iwms-frontend/src/utils/urlScope.ts (slugifyName / projectSlugs): a reserved
word or a repeated name gets -2, -3, ... in unique_id order. Always computed
over ALL live projects of the company, so every user sees the same slug.
"""
import re
import unicodedata

RESERVED = {
    "", "auth", "login", "admin", "dashboard", "district", "localbody", "audits",
    "api", "static", "media", "superadmin",
}
_COMBINING = re.compile("[̀-ͯ]")


def slugify_name(name):
    s = unicodedata.normalize("NFKD", (name or "").lower())
    s = _COMBINING.sub("", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:50].rstrip("-")


def project_slugs(projects):
    """{unique_id: slug} for an iterable of (unique_id, name)."""
    out, taken = {}, set()
    for unique_id, name in sorted(projects, key=lambda p: p[0]):
        base = slugify_name(name) or "project"
        slug, n = base, 2
        while slug in RESERVED or slug in taken:
            slug, n = f"{base}-{n}", n + 1
        taken.add(slug)
        out[unique_id] = slug
    return out


def company_project_slugs(company_unique_id):
    """Slugs of the company's live projects (current DB alias)."""
    from app.models.superadmin_masters.project import Project

    rows = Project.objects.filter(
        company_id=company_unique_id, is_active=True, is_deleted=False,
    ).values_list("unique_id", "name")
    return project_slugs(rows)

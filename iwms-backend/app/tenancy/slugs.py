"""Company slug and database-name rules.

"Tata Motors" -> slug "tata-motors" -> DB "iwms_company_tata_motors".
"""
import re

from django.conf import settings
from django.utils.text import slugify

SAFE_DB_NAME = re.compile(r"^[a-z0-9_]{1,64}$")
SLUG_MAX_LENGTH = 40


def validate_db_name(db_name):
    """Raise ValueError unless db_name is safe to put into CREATE/DROP DATABASE."""
    if not isinstance(db_name, str) or not SAFE_DB_NAME.match(db_name):
        raise ValueError(f"Invalid database name: {db_name!r}")
    return db_name


def is_reserved_slug(slug):
    return slug in settings.RESERVED_SLUGS


def make_slug(name, *, exclude_pk=None):
    """A free, non-reserved slug for `name`; duplicates get -2, -3, ...

    Checks every company row, including soft-deleted ones, so a slug (and so
    a database name) is never reused while its old row still exists.
    """
    from app.models.superadmin_masters.company import Company

    base = slugify(name or "")[:SLUG_MAX_LENGTH].strip("-") or "company"
    taken = Company.objects.exclude(pk=exclude_pk) if exclude_pk else Company.objects.all()
    slug, n = base, 1
    while is_reserved_slug(slug) or taken.filter(slug=slug).exists():
        n += 1
        suffix = f"-{n}"
        slug = f"{base[:SLUG_MAX_LENGTH - len(suffix)].rstrip('-')}{suffix}"
    return slug


def db_name_for_slug(slug):
    return validate_db_name(settings.TENANT_DB_PREFIX + slug.replace("-", "_"))

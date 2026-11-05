"""Company logo upload (Company.company_logo) and brand colour."""
import re
import secrets

from django.conf import settings
from rest_framework.exceptions import ValidationError

# Real type from the first bytes, not the name. No SVG (it can carry scripts).
_SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
]
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _detect(head):
    for sig, ext in _SIGNATURES:
        if head.startswith(sig):
            return ext
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def set_logo(company, upload):
    if upload is None:
        raise ValidationError({"logo": "Choose an image file."})
    if upload.size > settings.LOGO_MAX_BYTES:
        raise ValidationError({"logo": f"Logo must be under {settings.LOGO_MAX_BYTES // (1024 * 1024)} MB."})
    head = upload.read(16)
    upload.seek(0)
    ext = _detect(head)
    if not ext:
        raise ValidationError({"logo": "Use a PNG, JPG, WebP or GIF image."})
    old = company.company_logo.name if company.company_logo else None
    # New random name every time, so browsers never show a cached old logo.
    company.company_logo.save(f"{company.slug}-{secrets.token_hex(6)}.{ext}", upload, save=False)
    company.save(update_fields=["company_logo", "updated_at"])
    if old:
        company.company_logo.storage.delete(old)
    return company


def remove_logo(company):
    if company.company_logo:
        company.company_logo.delete(save=False)
        company.company_logo = None
        company.save(update_fields=["company_logo", "updated_at"])
    return company


def clean_theme_color(value):
    value = (value or "").strip()
    if not HEX_COLOR.match(value):
        raise ValidationError("Use a colour like #4f46e5.")
    return value.lower()


def logo_url(company, request=None):
    if not company.company_logo:
        return None
    url = company.company_logo.url
    return request.build_absolute_uri(url) if request is not None else url

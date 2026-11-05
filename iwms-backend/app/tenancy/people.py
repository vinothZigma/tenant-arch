"""Company logins: company admins, staff logins and the hidden developer.

Company users are IWMS staff rows (StaffcreationOfficeDetails) in the
company DB: the same login works in the company workspace and in the IWMS
screens. A company admin has is_company_admin; the developer is_developer.

Staff rows are saved inside tenant_context(alias): their save() relies on the
router (display ids, Account row, QR code).
"""
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from rest_framework.exceptions import ValidationError

from app.models.superadmin.staff_management.staffcreation import Staffcreation

from .context import tenant_context

MIN_PASSWORD = 6


def developer_username():
    return settings.DEVELOPER_USERNAME


def check_not_reserved(username, field="username"):
    if username and username.strip().lower() == developer_username().lower():
        raise ValidationError({field: f'"{username}" is reserved. Choose another username.'})


def username_taken(alias, username, exclude_pk=None):
    qs = Staffcreation.objects.using(alias).filter(username__iexact=username)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.exists()


def check_password_rules(password, field="password"):
    if len(password or "") < MIN_PASSWORD:
        raise ValidationError({field: f"Password must be at least {MIN_PASSWORD} characters."})


def clean_email(email, field="email"):
    email = (email or "").strip()
    if email:
        try:
            validate_email(email)
        except DjangoValidationError:
            raise ValidationError({field: "Enter a valid email address."})
    return email


def login_json(staff, extra=None):
    data = {
        "id": staff.pk,
        "username": staff.username,
        "full_name": staff.employee_name,
        "email": staff.office_email or "",
        "is_company_admin": bool(staff.is_company_admin),
        "is_developer": bool(staff.is_developer),
        "is_active": bool(staff.login_enabled),
        "last_login": staff.last_login_at,
        "created_at": staff.created_at,
        "employee_id": staff.pk,
        "emp_id": staff.emp_id,
        "project_id": staff.project_id,
    }
    if extra:
        data.update(extra)
    return data


def clean_new_admins(new_admins, alias, taken=()):
    """[{username, password, full_name, email}] -> cleaned list (validated)."""
    cleaned, seen = [], {t.lower() for t in taken}
    for a in new_admins or []:
        if not isinstance(a, dict):
            raise ValidationError({"admins": "Invalid admin."})
        username = (a.get("username") or "").strip()
        if not username:
            raise ValidationError({"admins": "Every new admin needs a username."})
        check_not_reserved(username, "admins")
        if username.lower() in seen or username_taken(alias, username):
            raise ValidationError({"admins": f'Username "{username}" is already taken.'})
        check_password_rules(a.get("password"), "admins")
        email = clean_email(a.get("email"), "admins")
        seen.add(username.lower())
        cleaned.append({
            "username": username[:150], "password": a["password"],
            "full_name": ((a.get("full_name") or "").strip() or username)[:200], "email": email,
        })
    return cleaned


def create_login(alias, company, *, username, password, full_name, email="", project_id=None,
                 is_company_admin=False, is_developer=False, actor_id=None):
    """A staff row with its login switched on."""
    with tenant_context(alias):
        staff = Staffcreation(
            employee_name=full_name or username,
            username=username,
            office_email=email or None,
            password=make_password(password),
            login_enabled=True,
            approval_status=Staffcreation.APPROVAL_APPROVED,
            company_id=company.unique_id,
            project_id=project_id,
            is_company_admin=is_company_admin,
            is_developer=is_developer,
            created_by_id=actor_id,
            updated_by_id=actor_id,
        )
        from .iwms_scope import iwms_role_defaults

        iwms_role_defaults(staff, alias)  # user type + role, so the IWMS sign-in accepts it
        staff.save()
    return staff


def create_admins(cleaned, alias, company, project_id=None):
    return [
        create_login(alias, company, username=a["username"], password=a["password"],
                     full_name=a["full_name"], email=a["email"], project_id=project_id,
                     is_company_admin=True)
        for a in cleaned
    ]


def ensure_developer(company, reset_password=True):
    """Create (or refresh) the company's hidden developer account."""
    from .db import register_tenant_db

    if not settings.DEVELOPER_PASSWORD:
        return "skipped: DEVELOPER_PASSWORD is not set"
    alias = register_tenant_db(company.db_name)
    user = Staffcreation.objects.using(alias).filter(username__iexact=developer_username()).first()
    if user and not user.is_developer:
        return f'skipped: a normal user is already called "{developer_username()}"'
    if user is None:
        create_login(alias, company, username=developer_username(), password=settings.DEVELOPER_PASSWORD,
                     full_name="Developer", is_company_admin=True, is_developer=True)
        return "created"
    with tenant_context(alias):
        if reset_password:
            user.password = make_password(settings.DEVELOPER_PASSWORD)
        user.login_enabled = True
        user.is_deleted = False
        user.is_active = True
        user.save(update_fields=["password", "login_enabled", "is_deleted", "is_active", "updated_at"])
    return "updated"

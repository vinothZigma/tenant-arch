"""The "Login" switch on Staff Creation and Staff Access Configuration.

Off: the staff member can't sign in, and any open session ends on its next
request (app/authentication/jwt.py checks login_enabled every time).
On: they can sign in again; a still-PENDING staff member is approved (the
person switching it on is the approver, as in StaffcreationSerializer).
"""
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from app.models.superadmin.staff_management.staffcreation import Staffcreation


def parse_switch(value):
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "on", "yes"):
        return True
    if text in ("0", "false", "off", "no"):
        return False
    raise ValidationError({"login_enabled": "Send true or false."})


def set_staff_login(staff, enabled, request):
    me = getattr(request, "user", None)
    if not enabled and isinstance(me, Staffcreation) and me.pk == staff.pk:
        raise ValidationError({"login_enabled": "You can't switch off your own login."})
    fields = ["login_enabled", "updated_at"]
    staff.login_enabled = enabled
    if enabled:
        staff.failed_login_attempts = 0
        fields.append("failed_login_attempts")
        if staff.approval_status == Staffcreation.APPROVAL_PENDING:
            staff.approval_status = Staffcreation.APPROVAL_APPROVED
            staff.approved_at = timezone.now()
            fields += ["approval_status", "approved_at"]
    staff.save(update_fields=fields)
    return {"staff_unique_id": staff.staff_unique_id, "login_enabled": staff.login_enabled}

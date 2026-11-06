"""DRF exception handler: TenantNotSelected -> a readable 400."""
import logging

from django.db import DatabaseError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from .context import TenantNotSelected


logger = logging.getLogger(__name__)


def exception_handler(exc, context):
    if isinstance(exc, TenantNotSelected):
        # Logged with the traceback: it points at code that still needs a company.
        logger.warning("Company table queried with no company selected", exc_info=exc)
        return Response(
            {"detail": "This data belongs to a company. Choose a company first "
                       "(sign in at /<company>/login, or pick one in the company selector)."},
            status=400,
        )
    if isinstance(exc, DatabaseError) and _missing_schema(exc):
        # A table/column dropped because its module/field is turned off for
        # this company (app/tenancy/iwms_tables.py).
        logger.info("Query hit a dropped table/column: %s", exc, exc_info=exc)
        return Response(
            {"detail": "This data belongs to a module or field that is turned off for this company. "
                       "Ask the super admin to turn it on for your project."},
            status=409,
        )
    return drf_exception_handler(exc, context)


def _missing_schema(exc):
    code = exc.args[0] if exc.args else None
    if code in (1146, 1054):  # MySQL: no such table / unknown column
        return True
    text = str(exc).lower()
    return "no such table" in text or "no such column" in text

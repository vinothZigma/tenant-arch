"""DRF exception handler: TenantNotSelected -> a readable 400."""
import logging

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
    return drf_exception_handler(exc, context)

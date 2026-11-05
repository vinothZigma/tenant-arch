"""Run background work once per company in per_db mode.

Threads and management commands start with no company selected. In per_db
mode they must process each company in its own DB; in shared mode (or when a
company is already selected) the work runs once, as before.
"""
import logging

from django.core.management.base import BaseCommand

from .context import get_current_tenant_db, tenant_context
from .db import register_tenant_db
from .router import per_db_mode

logger = logging.getLogger(__name__)


def companies():
    from app.models.superadmin_masters.company import Company

    return Company.objects.filter(is_deleted=False, is_active=True).exclude(db_name=None).order_by("name")


def run_per_company(fn, *args, only=None, **kwargs):
    """fn(*args, **kwargs) once per company DB. Returns {slug: result} in
    per_db mode, or fn's result when it ran once. A failing company is
    logged and reported, and doesn't stop the others."""
    if not per_db_mode() or get_current_tenant_db():
        return fn(*args, **kwargs)
    results = {}
    for company in companies():
        if only and only not in (company.slug, company.unique_id):
            continue
        try:
            with tenant_context(register_tenant_db(company.db_name)):
                results[company.slug] = fn(*args, **kwargs)
        except Exception as exc:
            logger.exception("Job failed for company %s", company.slug)
            results[company.slug] = {"error": str(exc)}
    return results


class PerCompanyCommand(BaseCommand):
    """A management command whose handle() runs once per company (per_db)."""

    def create_parser(self, prog_name, subcommand, **kwargs):
        parser = super().create_parser(prog_name, subcommand, **kwargs)
        parser.add_argument("--company", help="Only this company (slug or unique_id).")
        return parser

    def execute(self, *args, **options):
        only = options.get("company")
        if not per_db_mode() or get_current_tenant_db():
            return super().execute(*args, **options)
        outputs = []
        for company in companies():
            if only and only not in (company.slug, company.unique_id):
                continue
            self.stdout.write(f"== {company.name}")
            try:
                with tenant_context(register_tenant_db(company.db_name)):
                    out = super().execute(*args, **options)
                if out:
                    outputs.append(out)
            except Exception as exc:  # keep going with the other companies
                self.stderr.write(f"FAILED for {company.name}: {exc}")
        return "\n".join(outputs) if outputs else None

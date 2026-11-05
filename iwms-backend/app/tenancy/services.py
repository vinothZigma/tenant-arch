"""Provision and remove company databases."""
from .backup import backup_database
from .db import create_tenant_db, drop_tenant_db, tenant_db_exists


def provision_company(*, name, description=None, verbosity=0):
    """Create a Company row and its database with every company table.

    On any failure the new database is dropped and the row deleted, so a
    half-created company never stays behind.
    """
    from app.models.superadmin_masters.company import Company

    company = Company.objects.create(name=name, description=description)
    try:
        alias = create_tenant_db(company.db_name, verbosity=verbosity)
        from .shared_rows import copy_shared_rows

        copy_shared_rows(alias)
    except Exception:
        drop_tenant_db(company.db_name)
        Company.objects.filter(pk=company.pk).delete()
        raise
    return company


def ensure_company_db(company, verbosity=0):
    """Create/migrate the DB of an existing company (idempotent). Returns the alias."""
    if not company.db_name:
        company.save()  # Company.save() fills slug and db_name
    return create_tenant_db(company.db_name, verbosity=verbosity)


def deprovision_company(company, *, delete_row=False, backup=True):
    """Back up and drop a company's database; optionally hard-delete its row.

    Returns the backup path (or None when there was nothing to back up).
    """
    from app.models.superadmin_masters.company import Company

    path = None
    if company.db_name and tenant_db_exists(company.db_name):
        if backup:
            path = backup_database(company.db_name, reason="drop-company-db")
        drop_tenant_db(company.db_name)
    if delete_row:
        # Queryset delete: a hard delete, bypassing the soft-delete cascade.
        Company.objects.filter(pk=company.pk).delete()
    return path

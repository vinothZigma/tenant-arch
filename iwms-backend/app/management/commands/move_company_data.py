"""Copy one company's rows from the shared (master) DB into its own DB.

Used once per existing company when switching from TENANCY_MODE=shared to
per_db. The master DB is backed up first; the source rows are left in place
(so switching back to shared keeps working) — drop them later by hand once
per_db has run for a while.

Which rows: tables with a company_id column -> rows of this company, plus
the shared catalog (rows with no company, see app/tenancy/shared_rows.py). Tables
without one (child rows such as complaint history, trip waypoints) -> all
rows, but only when this is the only company in the master DB, or with
--include-unscoped.
"""
from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction
from django.db.models import Q

from app.models.superadmin_masters.company import Company
from app.tenancy.backup import backup_database
from app.tenancy.catalog import is_tenant_model
from app.tenancy.db import create_tenant_db
from app.tenancy.shared_rows import copy_shared_rows, keep_timestamps, no_fk_checks

BATCH = 500


class Command(BaseCommand):
    help = "Copy a company's data from the shared master DB into its own company DB."

    def add_arguments(self, parser):
        parser.add_argument("company", help="Company slug or unique_id.")
        parser.add_argument("--include-unscoped", action="store_true",
                            help="Also copy tables without company_id when several companies exist.")
        parser.add_argument("--replace", action="store_true",
                            help="Empty the company DB's tables first (default: refuse if not empty).")
        parser.add_argument("--no-backup", action="store_true")

    def handle(self, *args, **options):
        key = options["company"]
        company = Company.objects.filter(slug=key).first() or Company.objects.filter(unique_id=key).first()
        if not company:
            raise CommandError(f"No company with slug or id {key!r}.")
        only_company = Company.objects.filter(is_deleted=False).count() == 1
        copy_unscoped = only_company or options["include_unscoped"]

        tables = connections["default"].introspection.table_names()
        tenant_models = [
            m for m in apps.get_app_config("app").get_models()
            if is_tenant_model(m) and m._meta.managed and m._meta.db_table in tables
        ]
        alias = create_tenant_db(company.db_name)

        if not options["replace"]:
            busy = [m._meta.db_table for m in tenant_models
                    if m._base_manager.using(alias).exists()]
            if busy:
                raise CommandError(
                    f"{company.db_name} already has data ({', '.join(busy[:5])}...). Use --replace to overwrite.")

        if not options["no_backup"]:
            path = backup_database(connections["default"].settings_dict["NAME"], reason=f"move-{company.slug}")
            self.stdout.write(f"Backup of the master DB: {path}")

        total, skipped = 0, []
        with no_fk_checks(alias), transaction.atomic(using=alias):
            for model in tenant_models:
                fields = {f.attname for f in model._meta.concrete_fields}
                source = model._base_manager.using("default").all()
                if "company_id" in fields:
                    if copy_unscoped:  # the only company: no-company rows are its data too
                        source = source.filter(Q(company_id=company.unique_id) | Q(company_id__isnull=True))
                    else:
                        source = source.filter(company_id=company.unique_id)
                elif not copy_unscoped:
                    skipped.append(model._meta.db_table)
                    continue
                if options["replace"]:
                    model._base_manager.using(alias).all()._raw_delete(alias)
                else:
                    existing = set(model._base_manager.using(alias).values_list("pk", flat=True))
                    source = source.exclude(pk__in=existing) if existing else source
                n = 0
                with keep_timestamps(model):
                    batch = []
                    for row in source.iterator(chunk_size=BATCH):
                        row._state.db = alias
                        batch.append(row)
                        if len(batch) >= BATCH:
                            model._base_manager.using(alias).bulk_create(batch)
                            n += len(batch)
                            batch = []
                    if batch:
                        model._base_manager.using(alias).bulk_create(batch)
                        n += len(batch)
                total += n
                if n:
                    self.stdout.write(f"  {model._meta.db_table}: {n}")
            # M2M through tables auto-created by Django (if any company model has one).
            for model in tenant_models:
                for m2m in model._meta.local_many_to_many:
                    through = m2m.remote_field.through
                    if through._meta.auto_created and is_tenant_model(m2m.related_model):
                        rows = list(through.objects.using("default").all())
                        through.objects.using(alias).bulk_create(rows, ignore_conflicts=True)

        shared = copy_shared_rows(alias)
        for table, n in shared.items():
            self.stdout.write(f"  {table} (shared catalog): {n}")
        total += sum(shared.values())
        self.stdout.write(self.style.SUCCESS(f"Copied {total} rows into {company.db_name}."))
        if skipped:
            self.stdout.write(self.style.WARNING(
                "Not copied (no company_id; several companies share the master DB): " + ", ".join(skipped)))

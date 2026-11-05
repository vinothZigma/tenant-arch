from django.core.management.base import BaseCommand, CommandError

from app.models.superadmin_masters.company import Company
from app.tenancy.db import migrate_tenant_db, tenant_db_exists


class Command(BaseCommand):
    help = (
        "Apply migrations to every company database (company tables only). "
        "Run after `migrate` whenever a migration changes company models."
    )

    def add_arguments(self, parser):
        parser.add_argument("--company", help="Only this company (slug or unique_id).")
        parser.add_argument(
            "--create-missing", action="store_true",
            help="Also create company databases that don't exist yet.",
        )

    def handle(self, *args, **options):
        companies = Company.objects.filter(is_deleted=False).exclude(db_name=None).order_by("name")
        if options["company"]:
            key = options["company"]
            companies = companies.filter(slug=key) | companies.filter(unique_id=key)
            if not companies.exists():
                raise CommandError(f"No company with slug or id {key!r}.")

        verbosity = max(0, options["verbosity"] - 1)
        failed = 0
        done = 0
        for company in companies:
            label = f"{company.name} ({company.db_name})"
            try:
                if not tenant_db_exists(company.db_name):
                    if not options["create_missing"]:
                        self.stdout.write(self.style.WARNING(f"Skipped {label}: database missing (use --create-missing)"))
                        continue
                    from app.tenancy.db import create_tenant_db
                    create_tenant_db(company.db_name, verbosity=verbosity)
                else:
                    migrate_tenant_db(company.db_name, verbosity=verbosity)
                done += 1
                self.stdout.write(f"Migrated {label}")
            except Exception as exc:  # one broken company must not stop the rest
                failed += 1
                self.stderr.write(self.style.ERROR(f"FAILED {label}: {exc}"))

        summary = f"Done: {done} company database(s) migrated, {failed} failed."
        if failed:
            raise CommandError(summary)
        self.stdout.write(self.style.SUCCESS(summary))

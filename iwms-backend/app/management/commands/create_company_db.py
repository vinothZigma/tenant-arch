from django.core.management.base import BaseCommand, CommandError

from app.models.superadmin_masters.company import Company
from app.tenancy.db import tenant_table_names
from app.tenancy.services import ensure_company_db, provision_company


class Command(BaseCommand):
    help = (
        'Create a company database. `create_company_db "Tata Motors"` creates a new '
        "company and its DB; `create_company_db --company <slug|unique_id>` creates "
        "or migrates the DB of an existing company."
    )

    def add_arguments(self, parser):
        parser.add_argument("name", nargs="?", help="Name of a NEW company.")
        parser.add_argument("--company", help="Existing company (slug or unique_id).")
        parser.add_argument("--description", default=None)

    def handle(self, *args, **options):
        name, key = options["name"], options["company"]
        if bool(name) == bool(key):
            raise CommandError('Give either a new company name or --company <slug|unique_id>.')
        verbosity = max(0, options["verbosity"] - 1)

        if key:
            company = (
                Company.objects.filter(slug=key).first()
                or Company.objects.filter(unique_id=key).first()
            )
            if not company:
                raise CommandError(f"No company with slug or id {key!r}.")
            ensure_company_db(company, verbosity=verbosity)
        else:
            company = provision_company(
                name=name.strip(), description=options["description"], verbosity=verbosity
            )

        tables = tenant_table_names(company.db_name)
        self.stdout.write(self.style.SUCCESS(
            f"{company.name}: slug={company.slug} db={company.db_name} "
            f"unique_id={company.unique_id} ({len(tables)} tables)"
        ))

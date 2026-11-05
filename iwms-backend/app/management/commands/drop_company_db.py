from django.core.management.base import BaseCommand, CommandError

from app.models.superadmin_masters.company import Company
from app.tenancy.services import deprovision_company


class Command(BaseCommand):
    help = (
        "Back up a company's database into .backups/ and drop it. "
        "With --delete-company the company row is also hard-deleted."
    )

    def add_arguments(self, parser):
        parser.add_argument("company", help="Company slug or unique_id.")
        parser.add_argument("--delete-company", action="store_true")
        parser.add_argument("--yes", action="store_true", help="Don't ask for confirmation.")

    def handle(self, *args, **options):
        key = options["company"]
        company = (
            Company.objects.filter(slug=key).first()
            or Company.objects.filter(unique_id=key).first()
        )
        if not company:
            raise CommandError(f"No company with slug or id {key!r}.")

        if not options["yes"]:
            answer = input(
                f"Drop database {company.db_name} of {company.name!r}"
                f"{' and delete the company' if options['delete_company'] else ''}? "
                "A backup is taken first. Type the slug to confirm: "
            )
            if answer.strip() != company.slug:
                raise CommandError("Cancelled.")

        path = deprovision_company(company, delete_row=options["delete_company"])
        if path:
            self.stdout.write(f"Backup: {path}")
        self.stdout.write(self.style.SUCCESS(f"Dropped {company.db_name}."))

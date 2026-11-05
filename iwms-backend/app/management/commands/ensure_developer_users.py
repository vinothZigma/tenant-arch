from django.core.management.base import BaseCommand

from app.models.superadmin_masters.company import Company
from app.tenancy.people import developer_username, ensure_developer


class Command(BaseCommand):
    help = ("Create the hidden developer account in every company, or reset its password to "
            "DEVELOPER_PASSWORD from .env. Safe to run again.")

    def handle(self, *args, **options):
        for company in Company.objects.filter(is_deleted=False).exclude(db_name=None):
            try:
                result = ensure_developer(company)
            except Exception as exc:  # one broken company must not stop the rest
                result = f"FAILED: {exc}"
            self.stdout.write(f"{company.name}: {result}")
        self.stdout.write(self.style.SUCCESS(f"Developer username: {developer_username()}"))

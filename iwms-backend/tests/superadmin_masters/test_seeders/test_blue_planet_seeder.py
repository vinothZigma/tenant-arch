import pytest

from app.management.commands.seeders.superadmin_masters.blue_planet import (
    BluePlanetSeeder,
)
from app.management.commands.seeders.superadmin.staff_management.staff_office import (
    backfill_missing_staff_ids,
)
from app.models.masters.waste_masters.bins import Bins
from app.models.masters.customer_masters.customercreation import CustomerCreation
from app.models.core_modules.complaint_management.complaints import Complaint
from app.models.masters.ward import Ward
from app.models.masters.zone import Zone
from app.models.core_modules.schedule_setup.collection_point import Collection_point
from app.models.core_modules.schedule_setup.trip_plan import TripPlan
from app.models.superadmin_masters.company import Company
from app.models.superadmin_masters.project import Project
from app.models.masters.transport_masters.vehicleCreation import VehicleCreation
from app.models.superadmin.staff_management.staffcreation import Staffcreation


@pytest.mark.django_db
class TestBluePlanetSeeder:
    def test_creates_company_and_two_projects_with_noida_api_configuration(self):
        BluePlanetSeeder().run()

        company = Company.objects.get(name="Blue Planet")
        assert Company.objects.count() == 1

        projects = Project.objects.filter(company_id=company.unique_id).order_by("name")

        assert list(projects.values_list("name", flat=True)) == [
            "Blue Planet Integrated Waste Management",
            "Palakkad BP",
        ]

        noida = projects.get(name="Blue Planet Integrated Waste Management")
        assert noida.attendance_api_url == BluePlanetSeeder.ATTENDANCE_API_URL
        assert noida.attendance_api_key == BluePlanetSeeder.ATTENDANCE_API_KEY
        assert noida.gps_api_url == BluePlanetSeeder.GPS_API_URL
        assert noida.gps_vehicle_tracking_api == BluePlanetSeeder.GPS_VEHICLE_TRACKING_API
        assert noida.weighment_api_url == BluePlanetSeeder.WEIGHMENT_API_URL

        # Blue Planet Integrated Waste Management uses the real Vamosys-tracked fleet instead of
        # the generic 2-vehicle demo pattern used elsewhere.
        assert VehicleCreation.objects.filter(
            company_id=company.unique_id, project_id=noida.unique_id, is_active=True
        ).count() == len(BluePlanetSeeder.GNO_REAL_VEHICLES)
        assert set(
            VehicleCreation.objects.filter(company_id=company.unique_id, project_id=noida.unique_id, is_active=True)
            .values_list("vehicle_no", flat=True)
        ) == {v["vehicle_no"] for v in BluePlanetSeeder.GNO_REAL_VEHICLES}

        for project in projects:
            # The main project also gets the real ZNE3-GAMMA-01 zone + ward B
            # used by the Noida customer import.
            expected_geo = 3 + (1 if project.name == "Blue Planet Integrated Waste Management" else 0)
            assert Zone.objects.filter(company_id=company.unique_id, project_id=project.unique_id).count() == expected_geo
            assert Ward.objects.filter(company_id=company.unique_id, project_id=project.unique_id).count() == expected_geo
            assert Collection_point.objects.filter(company_id=company.unique_id, project_id=project.unique_id).count() == 3
            assert Bins.objects.filter(company_id=company.unique_id, project_id=project.unique_id).count() == 9
            if project.name != "Blue Planet Integrated Waste Management":
                assert VehicleCreation.objects.filter(company_id=company.unique_id, project_id=project.unique_id).count() == 2
            # Greater Noida (GNO, the main project) seeds no customers,
            # complaints or trip plans: those are created through the app.
            # The other project keeps the full demo set.
            is_gno = project.name == "Blue Planet Integrated Waste Management"
            scope = {"company_id": company.unique_id, "project_id": project.unique_id}
            assert CustomerCreation.objects.filter(**scope).count() == (0 if is_gno else 8)
            assert Complaint.objects.filter(**scope).count() == (0 if is_gno else 3)

            bin_trip_plans = TripPlan.objects.filter(**scope, collection_type=TripPlan.COLLECTION_TYPE_BIN)
            household_trip_plans = TripPlan.objects.filter(**scope, collection_type=TripPlan.COLLECTION_TYPE_HOUSEHOLD)
            if is_gno:
                assert not bin_trip_plans.exists()
                assert not household_trip_plans.exists()
            else:
                assert bin_trip_plans.get().is_auto_assign is True
                assert household_trip_plans.count() == 1
                assert all(plan.is_auto_assign for plan in household_trip_plans)

            for bin_obj in Bins.objects.filter(company_id=company.unique_id, project_id=project.unique_id):
                assert bin_obj.ward_id is not None
                assert bin_obj.zone_id is not None

    def test_is_idempotent(self):
        BluePlanetSeeder().run()
        BluePlanetSeeder().run()

        company = Company.objects.get(name="Blue Planet")
        assert Project.objects.filter(company_id=company.unique_id).count() == 2
        for project in Project.objects.filter(company_id=company.unique_id):
            expected_geo = 3 + (1 if project.name == "Blue Planet Integrated Waste Management" else 0)
            assert Zone.objects.filter(company_id=company.unique_id, project_id=project.unique_id).count() == expected_geo
            is_gno = project.name == "Blue Planet Integrated Waste Management"
            assert CustomerCreation.objects.filter(company_id=company.unique_id, project_id=project.unique_id).count() == (0 if is_gno else 8)
            assert Complaint.objects.filter(company_id=company.unique_id, project_id=project.unique_id).count() == (0 if is_gno else 3)

    def test_backfills_missing_staff_ids_across_all_projects(self):
        BluePlanetSeeder().run()
        staff = Staffcreation.objects.order_by("project_id", "staff_id").first()
        Staffcreation.objects.filter(pk=staff.pk).update(staff_id="")

        assert backfill_missing_staff_ids() == 1

        staff.refresh_from_db()
        assert staff.staff_id.startswith("STF")
        assert staff.staff_id != ""

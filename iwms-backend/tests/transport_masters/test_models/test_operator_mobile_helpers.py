"""Unit tests for the operator-mobile validation pipeline + auto-completion."""
from decimal import Decimal

import pytest
from django.utils import timezone

from app.models.masters.waste_masters.bins import Bins, BinType
from app.models.core_modules.schedule_setup.collection_point import Collection_point
from app.models.core_modules.daily_operations.daily_trip_assignment import DailyTripAssignment
from app.models.core_modules.daily_operations.daily_trip_collection_point import (
    DailyTripCollectionPoint,
)
from app.models.core_modules.schedule_setup.staff_template import StaffTemplate
from app.models.masters.panchayat import Panchayat, GeoFencingType
from app.models.masters.transport_masters.vehicleCreation import VehicleCreation
from app.models.superadmin.role_management.staffUserType import StaffUserType
from app.models.superadmin.staff_management.staffcreation import Staffcreation
from app.models.waste_collection_bluetooth.waste_collection_bluetooth import WasteType
from app.viewsets.operator_mobile.helpers import (
    OperatorFlowError,
    build_scan_context,
    find_active_assignment_for_operator,
    progress_payload,
    resolve_bin_from_qr,
    validate_bin_against_assignment,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
#
# Every *_id field on these models is a plain CharField holding the related
# row's unique_id (not a ForeignKey), so fixtures pass `.unique_id` /
# `.staff_unique_id` explicitly — passing a model instance would silently
# store its str() and break every lookup.
# ---------------------------------------------------------------------------


def _make_staff(user_type, role_name, username, company, project, district, city, zone, ward):
    role, _ = StaffUserType.objects.get_or_create(
        name=role_name,
        usertype_id=user_type.unique_id,
    )
    return Staffcreation.objects.create(
        employee_name=username,
        username=username,
        password="x",
        user_type_id=user_type.unique_id,
        staffusertype_id=role.unique_id,
        company_id=company.unique_id,
        project_id=project.unique_id,
        district_id=district.unique_id,
        city_id=city.unique_id,
        zone_id=zone.unique_id,
        ward_id=ward.unique_id,
        is_active=True,
        is_deleted=False,
    )


def _make_panchayat(name, lat, lng, state, district, city, company, project):
    return Panchayat.objects.create(
        panchayat_name=name,
        company_id=company.unique_id,
        project_id=project.unique_id,
        state_id=state.unique_id,
        district_id=district.unique_id,
        city_id=city.unique_id,
        geofencing_type=GeoFencingType.POLYGON,
        agreed_weight_kg=500,
        weight_unit="kg",
        latitude=Decimal(lat),
        longitude=Decimal(lng),
    )


def _make_cp(name, panchayat, lat, lng, state, district, city, company, project):
    return Collection_point.objects.create(
        cp_name=name,
        panchayat_id=panchayat.unique_id,
        state_id=state.unique_id,
        district_id=district.unique_id,
        city_id=city.unique_id,
        company_id=company.unique_id,
        project_id=project.unique_id,
        latitude=Decimal(lat),
        longitude=Decimal(lng),
    )


def _make_bin(name, qr, cp, waste_type, company, project):
    return Bins.objects.create(
        bin_name=name,
        bin_qr=qr,
        bin_capacity=240,
        bin_type=BinType.MEDIUM,
        bin_image="x.png",
        collection_point_id=cp.unique_id,
        wastetype_id=waste_type.unique_id,
        company_id=company.unique_id,
        project_id=project.unique_id,
    )


@pytest.fixture
def panchayat(db, state, district, city, company, project):
    return _make_panchayat("Test Panchayat", "13.10", "80.20", state, district, city, company, project)


@pytest.fixture
def other_panchayat(db, state, district, city, company, project):
    return _make_panchayat("Other Panchayat", "13.20", "80.30", state, district, city, company, project)


@pytest.fixture
def cp(db, panchayat, state, district, city, company, project):
    return _make_cp("CP-T-01", panchayat, "13.10", "80.20", state, district, city, company, project)


@pytest.fixture
def cp_in_other_panchayat(db, other_panchayat, state, district, city, company, project):
    return _make_cp("CP-OTH-01", other_panchayat, "13.20", "80.30", state, district, city, company, project)


@pytest.fixture
def waste_wet(db, company, project):
    return WasteType.objects.create(
        waste_type_name="Wet Waste", company_id=company.unique_id, project_id=project.unique_id
    )


@pytest.fixture
def waste_dry(db, company, project):
    return WasteType.objects.create(
        waste_type_name="Dry Waste", company_id=company.unique_id, project_id=project.unique_id
    )


@pytest.fixture
def bin_wet(db, cp, waste_wet, company, project):
    return _make_bin("CP Wet Bin", "QR-T-CP01-WET", cp, waste_wet, company, project)


@pytest.fixture
def bin_dry(db, cp, waste_dry, company, project):
    return _make_bin("CP Dry Bin", "QR-T-CP01-DRY", cp, waste_dry, company, project)


@pytest.fixture
def bin_wet_other(db, cp_in_other_panchayat, waste_wet, company, project):
    return _make_bin("Other CP Wet Bin", "QR-T-OTH-WET", cp_in_other_panchayat, waste_wet, company, project)


@pytest.fixture
def operator(db, user_type, company, project, district, city, zone, ward):
    return _make_staff(
        user_type, "Company Operator", "operator_t",
        company, project, district, city, zone, ward,
    )


@pytest.fixture
def driver(db, user_type, company, project, district, city, zone, ward):
    return _make_staff(
        user_type, "Company Driver", "driver_t",
        company, project, district, city, zone, ward,
    )


@pytest.fixture
def staff_template(db, driver, operator, company, project):
    return StaffTemplate.objects.create(
        driver_id=driver.staff_unique_id,
        operator_id=operator.staff_unique_id,
        company_id=company.unique_id,
        project_id=project.unique_id,
        extra_operator_id=[],
        status="ACTIVE",
    )


@pytest.fixture
def vehicle(db, company, project):
    return VehicleCreation.objects.create(
        vehicle_no="V-T-01",
        company_id=company.unique_id,
        project_id=project.unique_id,
        capacity=Decimal("1000.00"),
    )


@pytest.fixture
def assignment_wet(db, staff_template, panchayat, waste_wet, vehicle, company, project):
    # TripDefinition no longer exists; an assignment without a trip plan is
    # still valid and is what the helpers fall back to (no collection_type
    # narrowing, waste types read from `waste_type_ids`).
    return DailyTripAssignment.objects.create(
        company_id=company.unique_id,
        project_id=project.unique_id,
        staff_template_id=staff_template.unique_id,
        panchayat_id=panchayat.unique_id,
        waste_type_ids=[waste_wet.unique_id],
        vehicle_id=vehicle.unique_id,
        trip_date=timezone.localdate(),
        scheduled_time=timezone.localtime().time(),
        status=DailyTripAssignment.STATUS_SCHEDULED,
        approval_status=DailyTripAssignment.APPROVAL_APPROVED,
    )


@pytest.fixture
def trip_cp(db, assignment_wet, cp, bin_wet):
    return DailyTripCollectionPoint.objects.create(
        trip_assignment_id=assignment_wet.unique_id,
        collection_point_id=cp.unique_id,
        bin_id=bin_wet.unique_id,
        sequence=1,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestOperatorFlowValidation:
    def test_no_active_trip_raises(self, operator):
        with pytest.raises(OperatorFlowError) as exc:
            find_active_assignment_for_operator(operator)
        assert exc.value.code == "NO_ACTIVE_TRIP"

    def test_active_trip_found(self, operator, assignment_wet, trip_cp):
        result = find_active_assignment_for_operator(operator)
        assert result.unique_id == assignment_wet.unique_id

    def test_bin_not_found(self, operator, assignment_wet, trip_cp):
        with pytest.raises(OperatorFlowError) as exc:
            resolve_bin_from_qr("QR-UNKNOWN")
        assert exc.value.code == "BIN_NOT_FOUND"

    def test_wrong_waste_type(self, operator, assignment_wet, trip_cp, bin_dry):
        with pytest.raises(OperatorFlowError) as exc:
            validate_bin_against_assignment(bin_dry, assignment_wet)
        assert exc.value.code == "WRONG_WASTE_TYPE"

    def test_wrong_panchayat(self, operator, assignment_wet, trip_cp, bin_wet_other):
        with pytest.raises(OperatorFlowError) as exc:
            validate_bin_against_assignment(bin_wet_other, assignment_wet)
        assert exc.value.code == "WRONG_PANCHAYAT"

    def test_cp_not_in_trip(self, operator, assignment_wet, bin_wet):
        # No DailyTripCollectionPoint exists yet for assignment_wet ↔ cp
        with pytest.raises(OperatorFlowError) as exc:
            validate_bin_against_assignment(bin_wet, assignment_wet)
        assert exc.value.code == "CP_NOT_IN_TRIP"

    def test_already_collected(self, operator, assignment_wet, trip_cp, bin_wet):
        trip_cp.mark_collected(weight_kg=Decimal("10"), collected_by=operator.staff_unique_id)
        with pytest.raises(OperatorFlowError) as exc:
            validate_bin_against_assignment(bin_wet, assignment_wet)
        assert exc.value.code == "ALREADY_COLLECTED"

    def test_build_scan_context_happy_path(
        self, operator, assignment_wet, trip_cp, bin_wet
    ):
        # The app sends the bin unique_id (see serialize_bin_brief).
        ctx = build_scan_context(bin_wet.unique_id, operator)
        assert ctx.bin.unique_id == bin_wet.unique_id
        assert ctx.assignment.unique_id == assignment_wet.unique_id
        assert ctx.trip_cp.unique_id == trip_cp.unique_id


@pytest.mark.django_db
class TestTripAutoCompletion:
    def test_progress_payload_initial(self, assignment_wet, trip_cp):
        progress = progress_payload(assignment_wet)
        assert progress == {"collected": 0, "total": 1, "completed": False}

    def test_last_scan_resolves_trip_without_auto_ending(
        self, operator, assignment_wet, trip_cp
    ):
        # Ending a trip is now a driver-confirmed action (TripCompletionNudge):
        # mark_collected() calls mark_completed_if_all_cps_collected with
        # auto_end=False, so the trip stays open while progress reports done.
        trip_cp.mark_collected(weight_kg=Decimal("10"), collected_by=operator.staff_unique_id)
        assignment_wet.refresh_from_db()
        assert assignment_wet.status != DailyTripAssignment.STATUS_COMPLETED
        progress = progress_payload(assignment_wet)
        assert progress == {"collected": 1, "total": 1, "completed": True}

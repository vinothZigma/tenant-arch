"""Unit tests: small rules checked on their own, no database."""
import pytest
from rest_framework.exceptions import ValidationError

from app.tenancy.people import check_password_rules, clean_email
from app.tenancy.project_slugs import project_slugs, slugify_name
from app.tenancy.slugs import validate_db_name
from app.tenancy.validation import clean_record_data

pytestmark = pytest.mark.unit

FIELDS = [
    {"name": "name", "label": "Name", "type": "text", "required": True},
    {"name": "cost", "label": "Cost", "type": "number", "required": False},
    {"name": "email", "label": "Email", "type": "email", "required": False},
    {"name": "bought", "label": "Bought on", "type": "date", "required": False},
    {"name": "active", "label": "Active", "type": "boolean", "required": False},
]


# ---------------------------------------------------------------- project URL slugs

@pytest.mark.parametrize("name, expected", [
    ("Palakkad BP", "palakkad-bp"),
    ("  Café  Zone ", "cafe-zone"),
    ("North/South #1", "north-south-1"),
    ("", ""),
])
@pytest.mark.case("Project name becomes a URL slug")
def test_project_name_slug(case, name, expected):
    """The project part of /<company>/<project>/login is made from the project name."""
    case.check("slug", expected, slugify_name(name))


@pytest.mark.case("Reserved and repeated project names get -2, -3", data={
    "projects": "p1 'Admin', p2 'North', p3 'North', p4 'login'",
})
def test_project_slugs_reserved_and_duplicates(case):
    """'admin' and 'login' are app routes; the same name twice must still give unique URLs."""
    slugs = project_slugs([("p1", "Admin"), ("p2", "North"), ("p3", "North"), ("p4", "login")])
    case.check("p1 (Admin)", "admin-2", slugs["p1"])
    case.check("p2 (North)", "north", slugs["p2"])
    case.check("p3 (North again)", "north-2", slugs["p3"])
    case.check("p4 (login)", "login-2", slugs["p4"])


# ---------------------------------------------------------------- company DB names

@pytest.mark.parametrize("db_name, valid", [
    ("iwms_company_blue_planet", True),
    ("iwms_company_tata", True),
    ("bad-name", False),
    ("x; DROP DATABASE iwms", False),
    ("", False),
])
@pytest.mark.case("Company database name is safe for CREATE DATABASE")
def test_db_name_safety(case, db_name, valid):
    """Only lower-case letters, digits and _ may reach CREATE/DROP DATABASE."""
    case.given(db_name=db_name)
    try:
        validate_db_name(db_name)
        accepted = True
    except ValueError:
        accepted = False
    case.check("accepted", valid, accepted)


# ---------------------------------------------------------------- record validation

@pytest.mark.case("Valid record data is converted to the field types", data={
    "name": " Pump ", "cost": "1200", "email": "ops@example.com", "bought": "2026-11-18", "active": 1, "junk": "x",
})
def test_record_data_converted(case):
    data = {"name": " Pump ", "cost": "1200", "email": "ops@example.com", "bought": "2026-11-18",
            "active": 1, "junk": "x"}
    out = clean_record_data(FIELDS, data)
    case.check("name (trimmed)", "Pump", out["name"])
    case.check("cost (number)", 1200, out["cost"])
    case.check("bought (ISO date)", "2026-11-18", out["bought"])
    case.check("active (boolean)", True, out["active"])
    case.check("unknown key dropped", False, "junk" in out)


@pytest.mark.parametrize("data, field, message", [
    ({}, "name", "Name is required."),
    ({"name": "A", "cost": "abc"}, "cost", "Cost: invalid number."),
    ({"name": "A", "email": "not-an-email"}, "email", "Email: invalid email."),
    ({"name": "A", "bought": "18/11/2026"}, "bought", "Bought on: invalid date."),
    ({"name": "x" * 256}, "name", "Name: at most 255 characters."),  # 256 characters
])
@pytest.mark.case("Invalid record data gives a readable message")
def test_record_data_errors(case, data, field, message):
    with pytest.raises(ValidationError) as err:
        clean_record_data(FIELDS, data)
    got = err.value.detail.get(field, "")
    case.check(f"error on '{field}'", message, str(got[0] if isinstance(got, list) else got))


# ---------------------------------------------------------------- people rules

@pytest.mark.parametrize("password, ok", [("12345", False), ("123456", True), ("", False)])
@pytest.mark.case("Password needs at least 6 characters")
def test_password_rule(case, password, ok):
    case.given(password=password, length=len(password))
    try:
        check_password_rules(password)
        accepted = True
    except ValidationError:
        accepted = False
    case.check("accepted", ok, accepted)


@pytest.mark.parametrize("email, result", [
    ("  ops@example.com ", "ops@example.com"),
    ("", ""),
    ("ops@", None),
])
@pytest.mark.case("Email is trimmed and checked")
def test_clean_email(case, email, result):
    case.given(email=email)
    try:
        got = clean_email(email)
    except ValidationError:
        got = None
    case.check("cleaned email (None = refused)", result, got)

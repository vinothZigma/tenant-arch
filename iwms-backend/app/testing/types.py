"""The test types shown in Console → Testing, and how each test is sorted.

A test belongs to a type by its pytest marker (@pytest.mark.unit, ...) or,
for the older suite that has no markers, by the folder it lives in.
No Django imports here: the pytest plugin uses this before Django is set up.
"""

TYPES = [
    {
        "key": "unit",
        "label": "Unit testing",
        "icon": "box",
        "description": "Single functions and models on their own: slugs, validation, "
                       "model defaults, serializers.",
    },
    {
        "key": "integration",
        "label": "Integration testing",
        "icon": "layers",
        "description": "Parts working together: company databases, migrations, seeders, "
                       "permission sync between master and company DBs.",
    },
    {
        "key": "api",
        "label": "API testing",
        "icon": "link",
        "description": "HTTP endpoints: status codes, response bodies, authentication "
                       "and validation messages.",
    },
    {
        "key": "ui",
        "label": "UI testing",
        "icon": "dashboard",
        "description": "Real browser (headless Chrome) on the React app: pages render, "
                       "forms submit, errors show. Screenshots are saved.",
    },
    {
        "key": "functional",
        "label": "Functional testing",
        "icon": "check",
        "description": "Business flows end to end: create company → admin signs in → "
                       "plan limits → buy extra records.",
    },
    {
        "key": "security",
        "label": "Security testing",
        "icon": "key",
        "description": "Attacks a safe system must refuse: forged / expired tokens, brute force, "
                       "cross-company access, SQL injection, XSS, path traversal, leaks, CORS.",
    },
    {
        "key": "regression",
        "label": "Regression testing",
        "icon": "shield",
        "description": "Every test except performance, compared with the previous "
                       "regression run: shows what broke, what got fixed and what is new.",
    },
    {
        "key": "performance",
        "label": "Performance testing",
        "icon": "arrowUp",
        "description": "Response times of key endpoints over many requests "
                       "(average, p95, max) against a limit.",
    },
]
TYPE_KEYS = [t["key"] for t in TYPES]
# Types a single test can have (regression is a run of the others).
CASE_TYPES = [k for k in TYPE_KEYS if k != "regression"]
REGRESSION_EXCLUDES = {"performance"}

# Older suite (no markers): folder name -> type.
_FOLDER_TYPES = {
    "test_models": "unit",
    "test_serializers": "unit",
    "test_utils": "unit",
    "test_api": "api",
    "login": "api",
    "test_seeders": "integration",
    "tenancy": "integration",
    "test_middleware": "integration",
}


def classify(nodeid, marker_names=()):
    for key in CASE_TYPES:
        if key in marker_names:
            return key
    path = nodeid.split("::", 1)[0].replace("\\", "/")
    parts = path.split("/")
    for part in reversed(parts[:-1]):
        if part in _FOLDER_TYPES:
            return _FOLDER_TYPES[part]
    if len(parts) == 2:          # tests/test_xxx.py: whole-feature checks
        return "functional"
    return "integration"


def area_of(nodeid):
    """Feature area shown in the UI: 'tests/masters/test_api/x.py' -> 'Masters'."""
    parts = nodeid.split("::", 1)[0].replace("\\", "/").split("/")
    if len(parts) > 2 and parts[0] == "tests":
        name = parts[1]
        if name == "console":
            name = parts[-1].removesuffix(".py").removeprefix("test_").split("_", 1)[-1]
    else:
        name = parts[-1].removesuffix(".py").removeprefix("test_")
    return name.replace("_", " ").strip().capitalize() or "General"


def humanize(test_name):
    """'test_wrong_password_is_refused[case-1]' -> 'Wrong password is refused (case-1)'."""
    name, _, param = test_name.partition("[")
    text = name.removeprefix("test_").replace("_", " ").strip().capitalize()
    return f"{text} ({param.rstrip(']')})" if param else text

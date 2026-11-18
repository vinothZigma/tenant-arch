# 16 · Console → Testing

The super admin console (`/superadmin/testing`) runs the automatic tests with
one click and shows every test case with its input data and result.

## Test types

| Button | What runs |
|---|---|
| Unit testing | `@pytest.mark.unit` tests + older tests in `test_models/`, `test_serializers/`, `test_utils/` |
| Integration testing | `@pytest.mark.integration` + `tenancy/`, `test_seeders/`, `test_middleware/` and other feature folders |
| API testing | `@pytest.mark.api` + `test_api/`, `login/` |
| UI testing | `@pytest.mark.ui`: headless Chrome (Playwright) on the React app |
| Functional testing | `@pytest.mark.functional` + `tests/test_*.py` (whole-feature checks) |
| Security testing | `@pytest.mark.security` (`tests/console/test_security.py`): forged / tampered / expired / `alg:none` tokens, user enumeration, brute force, HttpOnly cookie + portal-header CSRF guard, cross-company access, company token on console API, SQL injection, stored XSS / template text, DB-name injection, path traversal, password-hash leaks, tracebacks, security headers, CORS. A failing test is a finding to fix in the app |
| Regression testing | every test except performance, compared with the previous regression run (broke / fixed / new / still failing) |
| Performance testing | `@pytest.mark.performance`: 15–30 requests per endpoint, avg / p95 / max against a limit |

Sorting rules: `app/testing/types.py` (`classify`).

## Safety

* Runs use `config.test_settings` (in-memory SQLite master, company DBs as temp SQLite files).
* The plugin stops the run if any configured DB is not SQLite, and every new
  non-SQLite connection raises. Tests can never touch MySQL.
* UI tests start their own Django live server (SQLite) and their own Vite dev
  server (`iwms-frontend/vite.uitest.config.ts`, separate dependency cache)
  pointed at it, so the browser never talks to the real backend.

## What the page shows per test

* **Test input data**: `@pytest.mark.case("Title", data={...})`, `case.given(...)`;
  for older tests: parametrize values and fixture objects (company, project, …).
* **Checks**: every `assert` (expected / actual) and `case.check(name, expected, actual)`.
* **Verification failed**: Expected / Actual / Difference + traceback.
* **Database rows**: rows prepared before the test (test data) and rows the test wrote.
* **Performance**: `case.metric(...)` numbers. **Screenshots**: `case.screenshot(page, name)`.

## Writing a new test

```python
import pytest
pytestmark = pytest.mark.api          # type

@pytest.mark.case("Plans list returns the seeded plans", data={"endpoint": "GET /api/super/plans/"})
def test_plans_list(case, sup):
    r = sup.get("/api/super/plans/")
    case.check("status code", 200, r.status_code)
```

Fixtures for console tests (`platform`, `sup`, `create_company`, `company_client`)
are in `tests/console/conftest.py`.

## Files

* Backend: `app/testing/` (types, pytest plugin, runner, API at `/api/super/testing/...`)
* Tests: `tests/console/` (unit, integration, api, functional, performance, ui)
* Frontend: `iwms-frontend/src/tenancy/pages/testing.tsx`
* Results: `<repo>/.iwms-testing/console/runs/<run id>/` (last 40 runs kept, git-ignored)

## Setup on a new machine

* `uv pip install --python .venv/bin/python playwright` (done here). UI tests use
  Google Chrome if installed, else run `.venv/bin/playwright install chromium`.
* `npm install` in `iwms-frontend` (UI tests use its Vite).

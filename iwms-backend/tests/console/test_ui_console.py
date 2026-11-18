"""UI tests: the real React app in headless Chrome (Playwright).

Setup per run:
  * Django live server on the SQLite test DB (pytest-django `live_server`)
  * Vite dev server for iwms-frontend with VITE_API_LOCAL pointing at it,
    using vite.uitest.config.ts (own dependency cache)
  * Google Chrome (or Playwright's Chromium) headless
So the browser never talks to the real backend or MySQL.
Screenshots go to the run folder and show in Console → Testing.
"""
import os
import signal
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import pytest

from .conftest import ROOT_PASSWORD, ROOT_USER, create_company

pytestmark = [pytest.mark.ui, pytest.mark.django_db(transaction=True)]

# The live server shares ONE in-memory SQLite connection between its request
# threads. The console fires several requests at once, and concurrent queries
# on that connection now and then read garbage (seen as a random 401). Serve
# one request at a time instead; the browser just queues them.
from django.core.servers.basehttp import WSGIServer  # noqa: E402
from django.test.testcases import LiveServerThread  # noqa: E402

class _SerialWSGIServer(WSGIServer):
    """Non-threaded (so responses say Connection: close and requests queue).
    LiveServerThread already put the shared DB connections in its own thread."""

    def __init__(self, *args, connections_override=None, **kwargs):
        super().__init__(*args, **kwargs)


LiveServerThread.server_class = _SerialWSGIServer

BACKEND = Path(__file__).resolve().parents[2]
FRONTEND = Path(os.environ.get("IWMS_FRONTEND_DIR") or BACKEND.parent / "iwms-frontend")
TIMEOUT_MS = 60_000


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api", reason="Playwright is not installed")
    # Playwright's sync API runs an event loop in this thread; Django's ORM
    # would refuse to run next to it without this flag.
    os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome", headless=True)
        except Exception:  # noqa: BLE001 - no Google Chrome: Playwright's own Chromium
            try:
                b = p.chromium.launch(headless=True)
            except Exception as exc:  # noqa: BLE001
                pytest.skip(f"No browser available: {exc}. Run: .venv/bin/playwright install chromium")
        yield b
        b.close()


@pytest.fixture(scope="session")
def frontend(live_server, tmp_path_factory):
    vite = FRONTEND / "node_modules" / ".bin" / "vite"
    if not vite.exists():
        pytest.skip("iwms-frontend/node_modules is missing. Run `npm install` in iwms-frontend.")
    host = urlparse(live_server.url).hostname   # same host as the API so the auth cookie is same-site
    port = _free_port()
    env = {**os.environ, "VITE_PROD": "false", "VITE_API_LOCAL": f"{live_server.url}/api/v1", "BROWSER": "none"}
    log = open(tmp_path_factory.mktemp("vite") / "vite.log", "w")
    proc = subprocess.Popen(
        [str(vite), "--config", "vite.uitest.config.ts", "--host", host, "--port", str(port), "--strictPort"],
        cwd=str(FRONTEND), env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )
    url = f"http://{host}:{port}"
    deadline = time.time() + 90
    while True:
        try:
            urllib.request.urlopen(url, timeout=2)
            break
        except OSError:
            if proc.poll() is not None or time.time() > deadline:
                os.killpg(proc.pid, signal.SIGTERM)
                pytest.fail(f"Vite dev server did not start (see {log.name})")
            time.sleep(0.5)
    yield url
    os.killpg(proc.pid, signal.SIGTERM)
    proc.wait(timeout=10)
    log.close()


@pytest.fixture
def page(browser, frontend, request):
    context = browser.new_context(viewport={"width": 1366, "height": 820})
    pg = context.new_page()
    pg.set_default_timeout(TIMEOUT_MS)
    errors = []
    pg.on("pageerror", lambda exc: errors.append(str(exc)))
    pg.frontend = frontend
    pg.js_errors = errors
    yield pg
    rec = getattr(request.node, "_iwms", None)
    if rec and rec.get("status") in ("failed", "error"):
        try:
            from app.testing.pytest_plugin import Case
            Case(rec).screenshot(pg, "At failure")
        except Exception:  # noqa: BLE001
            pass
    context.close()


def sign_in(pg, username, password):
    pg.goto(pg.frontend + "/superadmin/login")
    pg.get_by_label("Username").fill(username)
    pg.locator('input[autocomplete="current-password"]').fill(password)
    pg.get_by_role("button", name="Sign in").click()


# ---------------------------------------------------------------- tests

@pytest.mark.case("Console sign-in page shows the form", data={"url": "/superadmin/login"})
def test_sign_in_page(case, platform, page):
    page.goto(page.frontend + "/superadmin/login")
    heading = page.get_by_role("heading", name="Super admin sign in")
    heading.wait_for()
    case.screenshot(page, "Sign-in page")
    case.check("heading visible", True, heading.is_visible())
    case.check("username field", True, page.get_by_label("Username").is_visible())
    case.check("password field", True, page.locator('input[autocomplete="current-password"]').is_visible())
    case.check("JavaScript errors", [], page.js_errors)


@pytest.mark.case("Wrong password shows an error and stays on sign-in", data={
    "url": "/superadmin/login", "username": ROOT_USER, "password": "wrong-pass",
})
def test_wrong_password(case, platform, page):
    sign_in(page, ROOT_USER, "wrong-pass")
    error = page.locator(".auth-form .error")
    error.wait_for()
    case.screenshot(page, "Error message")
    case.check("error shown", True, error.is_visible())
    case.check("still on the sign-in page", True, page.url.endswith("/superadmin/login"))


@pytest.mark.case("Super admin signs in and lands on the dashboard", data={
    "url": "/superadmin/login", "username": ROOT_USER, "password": ROOT_PASSWORD,
})
def test_sign_in_dashboard(case, platform, page):
    sign_in(page, ROOT_USER, ROOT_PASSWORD)
    heading = page.get_by_role("heading", name="Dashboard")
    heading.wait_for()
    case.screenshot(page, "Dashboard")
    case.check("dashboard heading", True, heading.is_visible())
    case.check("sidebar has Companies", True, page.get_by_role("navigation").get_by_role("link", name="Companies").is_visible())
    case.check("JavaScript errors", [], page.js_errors)


@pytest.mark.case("Companies page lists a company made through the API", data={
    "company": "Tata Motors", "url": "/superadmin/companies",
})
def test_companies_page(case, sup, page):
    create_company(sup, "Tata Motors")
    sign_in(page, ROOT_USER, ROOT_PASSWORD)
    page.get_by_role("heading", name="Dashboard").wait_for()
    page.get_by_role("navigation").get_by_role("link", name="Companies").click()
    page.get_by_role("heading", name="Companies").wait_for()
    row = page.get_by_role("main").get_by_role("link", name="Tata Motors", exact=True)
    row.wait_for()
    case.screenshot(page, "Companies list")
    case.check("company listed", True, row.is_visible())


@pytest.mark.case("Testing page shows the eight test types", data={"url": "/superadmin/testing"})
def test_testing_page(case, platform, page, monkeypatch, tmp_path):
    import json

    from app.testing import runner

    # Its own empty results folder + a ready test list, so the page never starts pytest.
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(runner, "LAST", tmp_path / "last_results.json")
    monkeypatch.setattr(runner, "CATALOG", tmp_path / "catalog.json")
    (tmp_path / "catalog.json").write_text(json.dumps({"mtime": 9e12, "collection_errors": [], "cases": [
        {"id": "tests/console/test_unit_rules.py::test_slug", "type": "unit", "area": "Rules",
         "title": "Project name becomes a URL slug", "description": "", "file": "tests/console/test_unit_rules.py",
         "data": {"name": "Palakkad BP"}, "expected": [{"check": "slug", "expected": "palakkad-bp"}], "fixtures": []},
        {"id": "tests/masters/test_api/test_x.py::test_list_needs_login", "type": "api", "area": "Masters",
         "title": "List unauthenticated returns 401", "description": "", "file": "tests/masters/test_api/test_x.py",
         "data": {"GET /api/v1/masters/areatypes/": "(no body)"}, "fixtures": ["superuser"],
         "expected": [{"check": "assert", "expected": "resp.status_code in (401, 403)"}]},
    ]}))
    (tmp_path / "last_results.json").write_text(json.dumps({
        "tests/masters/test_api/test_x.py::test_list_needs_login": {
            "status": "failed", "run": "r1", "at": "2026-11-18T10:48:25", "checks_passed": 0, "checks_total": 1,
            "error": "assert 404 in (401, 403)",
            "failed_check": {"name": "assert 404 in (401, 403)", "expected": "in (401, 403)", "actual": "404",
                             "passed": False}},
        "tests/console/test_unit_rules.py::test_slug": {
            "status": "passed", "run": "r1", "at": "2026-11-18T10:48:25", "checks_passed": 1, "checks_total": 1,
            "error": None, "failed_check": None},
    }))
    sign_in(page, ROOT_USER, ROOT_PASSWORD)
    page.get_by_role("heading", name="Dashboard").wait_for()
    page.get_by_role("navigation").get_by_role("link", name="Testing").click()
    page.get_by_role("heading", name="Testing", exact=True).wait_for()
    labels = ["Unit testing", "Integration testing", "API testing", "UI testing", "Functional testing",
              "Security testing", "Regression testing", "Performance testing"]
    page.get_by_role("heading", name=labels[0], exact=True).wait_for()
    case.screenshot(page, "Testing page")
    seen = [label for label in labels if page.get_by_role("heading", name=label, exact=True).is_visible()]
    case.check("test types shown", labels, seen)

    case.step("Open the Test cases & data tab")
    page.get_by_role("button", name="Test cases & data").click()
    page.get_by_role("columnheader", name="Expected result").wait_for()
    # textContent, not innerText: the CSS shows headers in capitals.
    headers = [h.strip() for h in page.locator("table.ts-cases thead th").all_text_contents() if h.strip()]
    case.check("columns", ["Test case", "Input data", "Expected result", "Last result / Error"], headers)
    case.check("error shown for the failed case", True, page.get_by_text("got").first.is_visible())
    case.screenshot(page, "Test cases with input, expected and error")
    case.step("Open the failed test case")
    page.get_by_text("List unauthenticated returns 401").click()
    page.get_by_role("dialog").get_by_text("Expected result").first.wait_for()
    wanted = ["1 Input data", "2 Expected result", "3 Actual result", "4 Error"]
    # Number badge + title (the grey hint after the title is ignored).
    blocks = [" ".join(b.split()) for b in page.get_by_role("dialog").locator(".ts-block h4").all_text_contents()]
    blocks = [b.replace(f"{b[0]}", f"{b[0]} ", 1) if b[1:2] != " " else b for b in blocks]
    case.check("popup blocks", wanted, [w if b.startswith(w) else b for b, w in zip(blocks, wanted)])
    case.screenshot(page, "Test case popup")

"""Performance tests: response times of key endpoints over many requests.

Runs on SQLite with Django's test client (no network), so the numbers are
the server-side cost of each request. Limits are set loosely enough for a
developer laptop; tighten them as the code gets faster.
"""
import statistics
import time

import pytest

from .conftest import company_client, create_company

pytestmark = [pytest.mark.performance, pytest.mark.django_db(transaction=True)]

REQUESTS = 30


def measure(case, label, call, requests=REQUESTS, p95_limit_ms=300.0):
    call()   # warm-up (first request loads URL conf, caches)
    times = []
    for _ in range(requests):
        start = time.perf_counter()
        status = call()
        times.append((time.perf_counter() - start) * 1000)
        assert status < 500, f"{label}: server error {status}"
    times.sort()
    p95 = times[max(0, int(len(times) * 0.95) - 1)]
    case.metric(requests=requests, avg_ms=round(statistics.mean(times), 1), median_ms=round(statistics.median(times), 1),
                p95_ms=round(p95, 1), max_ms=round(times[-1], 1), min_ms=round(times[0], 1),
                limit_p95_ms=p95_limit_ms, per_second=round(1000 / statistics.mean(times), 1))
    case.check(f"p95 of {label} ≤ {p95_limit_ms:.0f} ms", f"≤ {p95_limit_ms:.0f} ms", f"{p95:.1f} ms",
               ok=p95 <= p95_limit_ms)


@pytest.mark.case("Console companies list", data={
    "endpoint": "GET /api/super/companies/", "companies": 3, "requests": REQUESTS, "p95 limit": "400 ms",
})
def test_companies_list_speed(case, sup):
    for name in ("Perf One", "Perf Two", "Perf Three"):
        create_company(sup, name, admin=name.split()[1].lower())
    measure(case, "GET /api/super/companies/", lambda: sup.get("/api/super/companies/").status_code,
            p95_limit_ms=400)


@pytest.mark.case("Console plans list", data={"endpoint": "GET /api/super/plans/", "requests": REQUESTS,
                                              "p95 limit": "150 ms"})
def test_plans_list_speed(case, sup):
    measure(case, "GET /api/super/plans/", lambda: sup.get("/api/super/plans/").status_code, p95_limit_ms=150)


@pytest.mark.case("Company admin 'me' (permissions + usage)", data={
    "endpoint": "GET /api/t/<slug>/ws/me/", "requests": REQUESTS, "p95 limit": "300 ms",
})
def test_company_me_speed(case, sup):
    c = create_company(sup, "Speedy")
    api = company_client(c)
    measure(case, "GET ws/me/", lambda: api.get(f"/api/t/{c.slug}/ws/me/").status_code, p95_limit_ms=300)


@pytest.mark.case("Company record list with 50 rows", data={
    "endpoint": "GET …/modules/assets/records/", "rows": 50, "requests": REQUESTS, "p95 limit": "300 ms",
})
def test_record_list_speed(case, sup):
    c = create_company(sup, "Bulk")
    api = company_client(c)
    pid = api.get(f"/api/t/{c.slug}/ws/me/").data["projects"][0]["id"]
    url = f"/api/t/{c.slug}/ws/projects/{pid}/modules/assets/records/"
    for i in range(50):
        api.post(url, {"data": {"name": f"Asset {i}"}}, format="json")
    measure(case, "record list", lambda: api.get(url).status_code, p95_limit_ms=300)


@pytest.mark.case("Company admin login", data={"endpoint": "POST /api/t/<slug>/ws/auth/login/",
                                               "requests": 15, "p95 limit": "500 ms"})
def test_login_speed(case, sup):
    from .conftest import company_login

    c = create_company(sup, "Quick")
    measure(case, "login", lambda: company_login(c.slug, "boss").status_code, requests=15, p95_limit_ms=500)

"""pytest plugin for Console → Testing (loaded with -p app.testing.pytest_plugin).

Environment (set by app/testing/runner.py):
  IWMS_TEST_DIR   folder of this run (status.json, results.jsonl, screens/)
  IWMS_TEST_MODE  "run" (default) or "collect" (write catalog.json only)
  IWMS_TEST_TYPE  unit | integration | api | ui | functional | performance |
                  regression | all  -- keeps only tests of that type

For every test it records what the console shows:
  data      test input: @pytest.mark.case(data=...), case.given(...),
            parametrize values and simple fixture values
  checks    every assert (expected / actual) and case.check(...)
  db        rows created / changed / deleted while the test ran
  metrics   performance numbers (case.metric(...))
  screens   UI screenshots (case.screenshot(page, name))

Safety: the run stops if any database is not SQLite, and every new DB
connection that is not SQLite raises, so tests can never reach MySQL.
"""
import datetime
import decimal
import json
import os
import re
import time
import uuid
from pathlib import Path

import pytest

from app.testing.source_info import analyse, split_params
from app.testing.types import REGRESSION_EXCLUDES, area_of, classify, humanize

OUT_DIR = Path(os.environ["IWMS_TEST_DIR"]) if os.environ.get("IWMS_TEST_DIR") else None
MODE = os.environ.get("IWMS_TEST_MODE", "run")
RUN_TYPE = os.environ.get("IWMS_TEST_TYPE", "all")

MAX_DB_ROWS = 30
MAX_FIELDS = 14
MAX_CHECKS = 60
MAX_TEXT = 300
# Fixtures that are plumbing, not test data.
SKIP_FIXTURES = {
    "request", "settings", "db", "transactional_db", "django_db_setup", "django_db_blocker",
    "client", "api_client", "auth_client", "admin_client", "rf", "tmp_path", "tmp_path_factory",
    "monkeypatch", "capsys", "capfd", "caplog", "case", "mocker", "live_server", "page",
    "browser", "ui", "tenant_dirs", "env", "sup", "django_user_model", "django_assert_num_queries",
}


# ---------------------------------------------------------------- values

def _short(value):
    """JSON-safe, readable, short version of any value."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str) and len(value) > 120:
        return f"{value[:60]}… ({len(value)} characters)"
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()
    if isinstance(value, (decimal.Decimal, uuid.UUID)):
        return str(value)
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, dict):
        return {str(k): _short(v) for k, v in list(value.items())[:MAX_FIELDS]}
    if isinstance(value, (list, tuple, set)):
        items = [_short(v) for v in list(value)[:MAX_FIELDS]]
        return items + ["…"] if len(value) > MAX_FIELDS else items
    meta = getattr(value, "_meta", None)
    if meta is not None and hasattr(meta, "concrete_fields"):
        return f"{meta.object_name}: {_text(value)}"
    return _text(value)


def _text(value):
    try:
        text = str(value)
    except Exception:  # noqa: BLE001
        text = repr(value)
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "…"


def _row(instance):
    out = {}
    for f in instance._meta.concrete_fields:
        if f.name in ("password",):
            out[f.name] = "••••••"
            continue
        try:
            v = getattr(instance, f.attname)
        except Exception:  # noqa: BLE001
            continue
        if v in (None, "", [], {}):
            continue
        out[f.name] = _short(v)
        if len(out) >= MAX_FIELDS:
            break
    return out


# ---------------------------------------------------------------- per-test recorder

class Case:
    """The `case` fixture: lets a test describe its data and checks for the console."""

    def __init__(self, record):
        self._r = record

    def given(self, **fields):
        """Record test input data; returns it as a dict."""
        self._r["data"].update({k: _short(v) for k, v in fields.items()})
        return fields

    def step(self, text):
        self._r["steps"].append(_text(text))

    def check(self, name, expected, actual, ok=None):
        """Record a check and fail the test when it does not hold.

        ok: optional bool for checks that are not equality (e.g. time <= limit).
        """
        passed = (expected == actual) if ok is None else bool(ok)
        self._r["checks"].append({
            "name": _text(name), "expected": _short(expected), "actual": _short(actual), "passed": passed,
        })
        if not passed:
            raise AssertionError(f"{name}: expected {expected!r}, got {actual!r}")
        return actual

    def metric(self, **values):
        self._r["metrics"].update({k: _short(v) for k, v in values.items()})

    def screenshot(self, page, name):
        if OUT_DIR is None:
            return None
        folder = OUT_DIR / "screens"
        folder.mkdir(parents=True, exist_ok=True)
        file = f"{len(self._r['screens']) + 1:02d}-{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}"
        file = f"{file}-{uuid.uuid4().hex[:6]}.png"
        page.screenshot(path=str(folder / file), full_page=False)
        self._r["screens"].append({"name": _text(name), "file": file})
        return file


def _expected_entries(mark_expected, param_expected, info):
    out = []
    if isinstance(mark_expected, dict):
        out += [{"check": str(k), "expected": _text(_short(v))} for k, v in mark_expected.items()]
    elif mark_expected:
        out.append({"check": "result", "expected": _text(mark_expected)})
    out += [{"check": e["check"], "expected": e["expected"]} for e in info["expected"]]
    if not info["expected"]:
        out += [{"check": k, "expected": _text(repr(v))} for k, v in param_expected.items()]
    return out


def _inputs(item, params):
    try:
        from app.testing.form_inputs import build_inputs

        return [{**f, "fields": [{**x, "value": _short(x.get("value"))} for x in f["fields"]]}
                for f in build_inputs(item, params)]
    except Exception:  # noqa: BLE001 - analysis must never break a test
        return []


def _new_record(item):
    mark = item.get_closest_marker("case")
    title = (mark.args[0] if mark and mark.args else None) or (mark.kwargs.get("title") if mark else None)
    func = getattr(item, "function", None)
    doc = (func.__doc__ or "").strip() if func else ""
    params = dict(getattr(getattr(item, "callspec", None), "params", None) or {})
    param_data, param_expected = split_params(params)
    info = analyse(func, params) if func else {"expected": [], "inputs": {}}
    data = dict(mark.kwargs.get("data") or {}) if mark else {}
    data.update(param_data)
    inputs = _inputs(item, params)
    if not mark:
        # Older tests: requests / rows the test code sends, unless already shown as a form.
        sources = {f["source"] for f in inputs}
        made_in_test = any(f["source"] == "Created in the test" for f in inputs)
        for k, v in info["inputs"].items():
            if k in sources or (k.startswith("new ") and made_in_test):
                continue
            data.setdefault(k, v)
    return {
        "id": item.nodeid,
        "type": classify(item.nodeid, {m.name for m in item.iter_markers()}),
        "area": area_of(item.nodeid),
        "title": title or humanize(item.name),
        "description": doc.split("\n\n")[0].replace("\n", " ") if doc else "",
        "file": item.nodeid.split("::", 1)[0],
        "data": {k: _short(v) for k, v in data.items()},
        # Input as forms (model fields with labels and the values the test fills in).
        "inputs": inputs,
        "expected": _expected_entries(mark.kwargs.get("expected") if mark else None, param_expected, info),
        # Fixtures that build test data before the test (company, project, ...).
        "fixtures": [f for f in getattr(item, "fixturenames", ())
                     if f not in SKIP_FIXTURES and f not in params and not f.startswith(("_", "django_"))
                     and f not in ("pytestconfig", "recwarn", "doctest_namespace", "cache")],
        "steps": [],
        "checks": [],
        "db": [],
        "metrics": {},
        "screens": [],
    }


@pytest.fixture
def case(request):
    rec = getattr(request.node, "_iwms", None)
    if rec is None:
        rec = request.node._iwms = _new_record(request.node)
    return Case(rec)


# ---------------------------------------------------------------- DB write capture

_current = {"rec": None, "phase": None}


# Django's own bookkeeping rows are not test data.
NOISE_TABLES = {"django_migrations", "django_content_type", "auth_permission", "django_session",
                "django_admin_log"}


def _on_save(sender, instance, created, raw=False, **kw):
    rec = _current["rec"]
    if rec is None or len(rec["db"]) >= MAX_DB_ROWS or sender._meta.db_table in NOISE_TABLES:
        return
    try:
        rec["db"].append({
            "phase": _current["phase"], "action": "created" if created else "updated",
            "model": sender._meta.object_name, "table": sender._meta.db_table,
            "db": kw.get("using") or "default", "pk": _short(instance.pk), "fields": _row(instance),
        })
    except Exception:  # noqa: BLE001 - recording must never break a test
        pass


def _on_delete(sender, instance, **kw):
    rec = _current["rec"]
    if rec is None or len(rec["db"]) >= MAX_DB_ROWS or sender._meta.db_table in NOISE_TABLES:
        return
    try:
        rec["db"].append({
            "phase": _current["phase"], "action": "deleted", "model": sender._meta.object_name,
            "table": sender._meta.db_table, "db": kw.get("using") or "default",
            "pk": _short(instance.pk), "fields": {},
        })
    except Exception:  # noqa: BLE001
        pass


def _refuse_non_sqlite(sender, connection, **kw):
    if connection.vendor != "sqlite":
        raise RuntimeError(
            f"Console testing refuses to connect to {connection.vendor} database "
            f"'{connection.alias}'. Tests only run on SQLite.")


# ---------------------------------------------------------------- output files

def _write_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, default=str))
    os.replace(tmp, path)


class _Run:
    def __init__(self):
        self.status = {"total": 0, "done": 0, "passed": 0, "failed": 0, "error": 0, "skipped": 0,
                       "current": None, "phase": "collecting", "collection_errors": []}
        self.results = None

    def save_status(self):
        if OUT_DIR is not None:
            _write_json(OUT_DIR / "progress.json", self.status)

    def add(self, rec):
        st = self.status
        st["done"] += 1
        st[rec["status"]] = st.get(rec["status"], 0) + 1
        if self.results is None:
            self.results = open(OUT_DIR / "results.jsonl", "a", encoding="utf-8")
        self.results.write(json.dumps(rec, default=str) + "\n")
        self.results.flush()
        self.save_status()


RUN = _Run()


# ---------------------------------------------------------------- hooks

def pytest_configure(config):
    # Markers (case, unit, api, ...) are registered in pyproject.toml.
    if OUT_DIR is not None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)


def pytest_sessionstart(session):
    from django.conf import settings

    bad = {alias: cfg.get("ENGINE") for alias, cfg in settings.DATABASES.items()
           if "sqlite" not in (cfg.get("ENGINE") or "")}
    if bad:
        pytest.exit(f"Refusing to run: non-SQLite databases configured {bad}. "
                    f"Use DJANGO_SETTINGS_MODULE=config.test_settings.", returncode=3)
    from django.db.backends.signals import connection_created
    from django.db.models.signals import post_delete, post_save

    connection_created.connect(_refuse_non_sqlite, weak=False, dispatch_uid="iwms_testing_sqlite_only")
    post_save.connect(_on_save, weak=False, dispatch_uid="iwms_testing_save")
    post_delete.connect(_on_delete, weak=False, dispatch_uid="iwms_testing_delete")


def pytest_collectreport(report):
    if report.failed:
        RUN.status["collection_errors"].append({
            "file": report.nodeid, "error": _text_long(report.longreprtext, 4000),
        })


def _wanted(item):
    if RUN_TYPE in ("all", ""):
        return True
    t = classify(item.nodeid, {m.name for m in item.iter_markers()})
    if RUN_TYPE == "regression":
        return t not in REGRESSION_EXCLUDES
    return t == RUN_TYPE


def pytest_collection_modifyitems(session, config, items):
    keep, drop = [], []
    for item in items:
        (keep if _wanted(item) else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


def pytest_collection_finish(session):
    RUN.status["total"] = len(session.items)
    RUN.status["phase"] = "running"
    if OUT_DIR is None:
        return
    if MODE == "collect":
        cases = []
        for item in session.items:
            rec = _new_record(item)
            cases.append({k: rec[k] for k in ("id", "type", "area", "title", "description", "file", "data",
                                              "expected", "fixtures", "inputs")})
        _write_json(OUT_DIR / "catalog.json", {
            "cases": cases, "collection_errors": RUN.status["collection_errors"],
            "created": datetime.datetime.now().isoformat(timespec="seconds"),
        })
    RUN.save_status()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item):
    item._iwms = getattr(item, "_iwms", None) or _new_record(item)
    item._iwms_start = time.perf_counter()
    RUN.status["current"] = item.nodeid
    _current.update(rec=item._iwms, phase="setup")
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    rec = item._iwms
    # Parametrize values are already in rec["data"] / rec["expected"] (_new_record).
    for name, value in getattr(item, "funcargs", {}).items() if "case" not in item.fixturenames else ():
        if name in SKIP_FIXTURES or name.startswith(("_", "django_")) or name in rec["data"]:
            continue
        if callable(value) and not hasattr(value, "_meta"):
            continue
        if hasattr(value, "_meta") and rec.get("inputs"):
            continue   # fixture records are already shown as forms
        if value is None or isinstance(value, (str, int, float, bool, dict, list, tuple)) or hasattr(value, "_meta"):
            rec["data"][name] = _short(value)
    _current["phase"] = "test"
    yield
    _current["phase"] = "teardown"


def pytest_assertion_pass(item, lineno, orig, expl):
    rec = getattr(item, "_iwms", None)
    if rec is None or len(rec["checks"]) >= MAX_CHECKS:
        return
    expected, actual = _split_compare(orig, expl)
    rec["checks"].append({"name": _text(f"assert {orig}"), "expected": expected, "actual": actual,
                          "passed": True, "line": lineno})


_CMP = re.compile(r"^(.*?)\s(==|!=|<=|>=|<|>|in|not in|is|is not)\s(.*)$", re.S)


def _split_compare(orig, expl):
    """'r.status_code == 200' + '200 == 200' -> expected '== 200', actual '200'."""
    first = (expl or "").split("\n", 1)[0]
    m = _CMP.match(first)
    if m:
        return _expected_text(m.group(2), m.group(3)), _text(m.group(1))
    return "true", _text(first or orig)


def _expected_text(op, value):
    return _text(value) if op == "==" else f"{op} {_text(value)}"


def _text_long(text, limit):
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "\n…"


def _failure(report):
    text = report.longreprtext or ""
    crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
    message = crash.message if crash else (text.strip().splitlines() or [""])[-1]
    out = {"message": _text_long(message, 1500), "trace": _text_long(text, 8000)}
    m = re.search(r"assert (.+?) (==|!=|in|not in|<=|>=|<|>) (.+?)(?:\n|$)", message)
    if m:
        out["actual"], out["op"], out["expected"] = m.group(1), m.group(2), m.group(3)
    return out


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    rec = getattr(item, "_iwms", None)
    if rec is None:
        return
    if report.when == "call" or (report.when == "setup" and not report.passed) or (
            report.when == "teardown" and report.failed):
        if report.skipped:
            status = "skipped"
            rec["skip_reason"] = _text(report.longrepr[2] if isinstance(report.longrepr, tuple) else report.longreprtext)
        elif report.passed:
            status = "passed"
        elif report.when == "call":
            status = "failed"
        else:
            status = "error"
        if report.failed:
            rec["failure"] = _failure(report)
            rec["failure"]["when"] = report.when
            if not any(c for c in rec["checks"] if not c["passed"]):
                f = rec["failure"]
                first = f["message"].split("\n", 1)[0]
                if "op" in f:
                    check = {"name": first, "expected": _expected_text(f["op"], f["expected"]),
                             "actual": f["actual"]}
                else:
                    where = {"setup": "Test setup", "teardown": "Test clean-up"}.get(report.when, "Test")
                    check = {"name": f"{where} runs without an error", "expected": "no error",
                             "actual": _text(first)}
                rec["checks"].append({**check, "passed": False})
        if rec.get("status") in (None, "passed") or status in ("failed", "error"):
            rec["status"] = status


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    yield
    rec = getattr(item, "_iwms", None)
    _current.update(rec=None, phase=None)
    if rec is None or OUT_DIR is None or MODE == "collect":
        return
    rec.setdefault("status", "passed")
    rec["duration_ms"] = round((time.perf_counter() - getattr(item, "_iwms_start", time.perf_counter())) * 1000, 1)
    rec["db"] = [r for r in rec["db"] if r["phase"] != "teardown"]
    RUN.add(rec)


def pytest_sessionfinish(session, exitstatus):
    RUN.status["phase"] = "finished"
    RUN.status["current"] = None
    RUN.status["exit_code"] = int(exitstatus)
    if RUN.results is not None:
        RUN.results.close()
    RUN.save_status()

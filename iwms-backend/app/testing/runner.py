"""Start, watch and stop test runs for Console → Testing.

A run is a pytest child process (own process group) with the console plugin
loaded. Everything a run writes lives in <repo>/.iwms-testing/console/runs/<id>/:
  meta.json       type, who started it, when, pid, final status
  progress.json   counters + current test (written by the plugin)
  results.jsonl   one JSON line per finished test (written by the plugin)
  log.txt         pytest's own output
  screens/        UI screenshots
Only one run at a time. Nothing is kept in a database.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from .types import CASE_TYPES, TYPE_KEYS, TYPES

BACKEND = Path(__file__).resolve().parents[2]
FRONTEND = BACKEND.parent / "iwms-frontend"
ROOT = Path(os.environ.get("IWMS_TESTING_DIR") or BACKEND.parent / ".iwms-testing" / "console")
RUNS = ROOT / "runs"
CATALOG = ROOT / "catalog.json"
LAST = ROOT / "last_results.json"
KEEP_RUNS = 40

_lock = threading.Lock()
_procs = {}          # run id -> Popen (this server process only)


class RunError(Exception):
    pass


# ---------------------------------------------------------------- files

def _read(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, default=str, indent=1))
    os.replace(tmp, path)


def _run_dir(run_id):
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        raise RunError("Unknown run.")
    folder = RUNS / run_id
    if not (folder / "meta.json").exists():
        raise RunError("Unknown run.")
    return folder


def _alive(pid):
    if not pid:
        return False
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        return state not in ("Z", "X")
    except OSError:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _env(run_type, folder):
    env = dict(os.environ)
    env.update({
        "DJANGO_SETTINGS_MODULE": "config.test_settings",
        "IWMS_TEST_DIR": str(folder),
        "IWMS_TEST_TYPE": run_type,
        "IWMS_FRONTEND_DIR": str(FRONTEND),
        # Assert-pass recording needs freshly rewritten .pyc files; keep them apart.
        "PYTHONPYCACHEPREFIX": str(ROOT / "pycache"),
        "ENABLE_DAILY_TRIP_JOB_SCHEDULER": "false",
        "PYTHONUNBUFFERED": "1",
    })
    env.pop("DATABASE_URL", None)
    return env


def _pytest_cmd(targets):
    return [
        sys.executable, "-m", "pytest", *targets,
        "-p", "app.testing.pytest_plugin", "-p", "no:cacheprovider",
        "-o", "enable_assertion_pass_hook=true",
        "--continue-on-collection-errors", "--no-header", "-q", "-rN", "--tb=short",
    ]


# ---------------------------------------------------------------- runs

def active_run():
    for meta in list_runs(limit=5):
        if meta["status"] == "running":
            return meta
    return None


def start_run(run_type, test_ids=None, started_by=""):
    if run_type not in TYPE_KEYS and run_type != "selected":
        raise RunError(f"Unknown test type '{run_type}'.")
    test_ids = [t for t in (test_ids or []) if isinstance(t, str) and t.startswith("tests/")]
    if run_type == "selected" and not test_ids:
        raise RunError("Pick at least one test.")
    with _lock:
        busy = active_run()
        if busy:
            raise RunError(f"A {busy['label']} run is still going. Wait for it or stop it first.")
        now = datetime.now()
        run_id = f"{now:%Y%m%d-%H%M%S}-{run_type}"
        folder = RUNS / run_id
        folder.mkdir(parents=True, exist_ok=True)
        label = next((t["label"] for t in TYPES if t["key"] == run_type), "Selected tests")
        meta = {"id": run_id, "type": run_type, "label": label, "status": "running",
                "started": now.isoformat(timespec="seconds"), "finished": None,
                "started_by": started_by, "test_ids": test_ids}
        if run_type == "regression":
            base = _previous_regression(run_id)
            meta["baseline"] = base["id"] if base else None
        log = open(folder / "log.txt", "w", encoding="utf-8")
        proc = subprocess.Popen(
            _pytest_cmd(test_ids or ["tests"]), cwd=str(BACKEND), env=_env("all" if test_ids else run_type, folder),
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
        )
        log.close()
        meta["pid"] = proc.pid
        _write(folder / "meta.json", meta)
        _procs[run_id] = proc
    threading.Thread(target=_wait, args=(run_id, proc), daemon=True).start()
    _prune()
    return meta


def _wait(run_id, proc):
    proc.wait()
    _procs.pop(run_id, None)
    _finalize(RUNS / run_id)


def stop_run(run_id):
    folder = _run_dir(run_id)
    meta = _read(folder / "meta.json")
    if meta["status"] != "running":
        return meta
    meta["stop_requested"] = True
    _write(folder / "meta.json", meta)
    try:
        os.killpg(meta["pid"], signal.SIGTERM)
    except OSError:
        pass
    return meta


def _finalize(folder):
    """Set the final status once the child process is gone (idempotent)."""
    meta = _read(folder / "meta.json")
    if not meta or meta["status"] != "running":
        return meta
    progress = _read(folder / "progress.json", {}) or {}
    counts = _counts(folder)
    meta["finished"] = datetime.now().isoformat(timespec="seconds")
    meta["totals"] = counts
    if meta.get("stop_requested"):
        meta["status"] = "stopped"
    elif progress.get("phase") != "finished":
        meta["status"] = "crashed"
        meta["error"] = _log_tail(folder)
    elif progress.get("exit_code") == 3:
        meta["status"] = "refused"
        meta["error"] = _log_tail(folder)
    elif counts["failed"] or counts["error"]:
        # Files that could not be imported are shown as a warning on the run.
        meta["status"] = "failed"
    elif counts["total"] == 0:
        meta["status"] = "empty"
    else:
        meta["status"] = "passed"
    _write(folder / "meta.json", meta)
    _remember_last(meta, folder)
    return meta


def _log_tail(folder, lines=40):
    try:
        return "\n".join((folder / "log.txt").read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def _results(folder):
    out = []
    try:
        with open(folder / "results.jsonl", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass  # half-written last line while running
    except OSError:
        pass
    return out


def _counts(folder, results=None):
    results = _results(folder) if results is None else results
    c = {"total": len(results), "passed": 0, "failed": 0, "error": 0, "skipped": 0}
    for r in results:
        c[r.get("status", "error")] = c.get(r.get("status", "error"), 0) + 1
    return c


def _remember_last(meta, folder):
    last = _read(LAST, {}) or {}
    for r in _results(folder):
        failure = r.get("failure") or {}
        bad = next((c for c in r.get("checks", []) if not c.get("passed")), None)
        last[r["id"]] = {
            "status": r["status"], "run": meta["id"], "at": meta["finished"], "duration_ms": r.get("duration_ms"),
            "checks_passed": sum(1 for c in r.get("checks", []) if c.get("passed")),
            "checks_total": len(r.get("checks", [])),
            "error": failure.get("message", "").split("\n", 1)[0][:400] or r.get("skip_reason") or None,
            "failed_check": bad,
        }
    _write(LAST, last)


def _meta(folder):
    meta = _read(folder / "meta.json")
    if meta and meta["status"] == "running" and meta["id"] not in _procs and not _alive(meta.get("pid")):
        meta = _finalize(folder)
    return meta


def list_runs(limit=30):
    if not RUNS.exists():
        return []
    out = []
    for folder in sorted(RUNS.iterdir(), reverse=True):
        meta = _meta(folder) if (folder / "meta.json").exists() else None
        if meta:
            if meta["status"] == "running":
                meta["progress"] = _read(folder / "progress.json", {})
            out.append(meta)
        if len(out) >= limit:
            break
    return out


def _prune():
    runs = sorted(p for p in RUNS.iterdir() if p.is_dir()) if RUNS.exists() else []
    for folder in runs[:-KEEP_RUNS]:
        meta = _read(folder / "meta.json", {}) or {}
        if meta.get("status") != "running":
            shutil.rmtree(folder, ignore_errors=True)


def delete_run(run_id):
    folder = _run_dir(run_id)
    meta = _meta(folder)
    if meta["status"] == "running":
        raise RunError("Stop the run before deleting it.")
    shutil.rmtree(folder, ignore_errors=True)


LIGHT = ("id", "type", "area", "title", "status", "duration_ms")


def run_detail(run_id, since=0):
    """Run summary + light rows of the tests finished since row `since`."""
    folder = _run_dir(run_id)
    meta = _meta(folder)
    results = _results(folder)
    rows = []
    for r in results[since:]:
        row = {k: r.get(k) for k in LIGHT}
        row["message"] = (r.get("failure") or {}).get("message", "").split("\n", 1)[0][:200]
        rows.append(row)
    out = {**meta, "progress": _read(folder / "progress.json", {}) or {}, "counts": _counts(folder, results),
           "rows": rows, "since": since, "next": len(results)}
    if meta["type"] == "regression" and meta["status"] != "running":
        out["regression"] = _compare(meta, results)
    return out


def test_detail(run_id, test_id):
    folder = _run_dir(run_id)
    for r in _results(folder):
        if r["id"] == test_id:
            meta = _meta(folder)
            if meta["type"] == "regression" and meta.get("baseline"):
                prev = {x["id"]: x for x in _results(RUNS / meta["baseline"])}.get(test_id)
                r["previous_status"] = prev["status"] if prev else None
            return r
    raise RunError("That test is not in this run.")


def screenshot(run_id, name):
    folder = _run_dir(run_id)
    path = (folder / "screens" / name).resolve()
    if path.parent != (folder / "screens").resolve() or path.suffix != ".png" or not path.exists():
        raise RunError("No such screenshot.")
    return path


def run_log(run_id):
    return _log_tail(_run_dir(run_id), lines=400)


# ---------------------------------------------------------------- regression

def _previous_regression(exclude_id):
    for meta in list_runs(limit=KEEP_RUNS):
        if meta["type"] == "regression" and meta["id"] != exclude_id and meta["status"] in ("passed", "failed"):
            return meta
    return None


def _compare(meta, results):
    base_id = meta.get("baseline")
    if not base_id or not (RUNS / base_id / "meta.json").exists():
        return {"baseline": None}
    before = {r["id"]: r["status"] for r in _results(RUNS / base_id)}
    bad = ("failed", "error")
    groups = {"regressed": [], "fixed": [], "new": [], "still_failing": [], "removed": []}
    now_ids = set()
    for r in results:
        now_ids.add(r["id"])
        prev = before.get(r["id"])
        if prev is None:
            groups["new"].append(r["id"])
        elif prev == "passed" and r["status"] in bad:
            groups["regressed"].append(r["id"])
        elif prev in bad and r["status"] == "passed":
            groups["fixed"].append(r["id"])
        elif prev in bad and r["status"] in bad:
            groups["still_failing"].append(r["id"])
    groups["removed"] = [i for i in before if i not in now_ids]
    base_meta = _read(RUNS / base_id / "meta.json", {}) or {}
    return {"baseline": base_id, "baseline_started": base_meta.get("started"), **groups}


# ---------------------------------------------------------------- catalog

def _tests_mtime():
    newest = 0.0
    # Test files and the plugin / source reader that turn them into cases.
    for p in [*(BACKEND / "tests").rglob("*.py"), *(BACKEND / "app" / "testing").glob("*.py")]:
        newest = max(newest, p.stat().st_mtime)
    return newest


def catalog(refresh=False, cached_only=False):
    """Every test case with its type, title and input data (from pytest --collect-only).

    cached_only: return the saved list (even if test files changed since) or
    None, never start pytest.
    """
    cached = _read(CATALOG)
    if cached_only:
        if not cached:
            return None
        data = _with_last(cached)
        data["stale"] = cached.get("mtime", 0) < _tests_mtime()
        return data
    if cached and not refresh and cached.get("mtime", 0) >= _tests_mtime():
        return _with_last(cached)
    folder = ROOT / "collect"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    env = _env("all", folder)
    env["IWMS_TEST_MODE"] = "collect"
    proc = subprocess.run(
        _pytest_cmd(["tests"]) + ["--collect-only"], cwd=str(BACKEND), env=env,
        capture_output=True, text=True, timeout=600,
    )
    data = _read(folder / "catalog.json")
    if not data:
        raise RunError("Could not list the tests:\n" + (proc.stdout + proc.stderr)[-3000:])
    data["mtime"] = _tests_mtime()
    _write(CATALOG, data)
    return _with_last(data)


def _with_last(data):
    last = _read(LAST, {}) or {}
    for c in data["cases"]:
        c["last"] = last.get(c["id"])
    data["summary"] = {k: sum(1 for c in data["cases"] if c["type"] == k) for k in CASE_TYPES}
    data["summary"]["regression"] = sum(v for k, v in data["summary"].items() if k != "performance")
    return data


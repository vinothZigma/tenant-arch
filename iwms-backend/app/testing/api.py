"""Console → Testing API: /api/super/testing/...

GET    testing/                      types, test counts per type, active run, recent runs
GET    testing/cases/?refresh=1      every test case (type, title, input data, last result)
POST   testing/runs/                 {"type": "unit"} or {"type": "selected", "tests": [ids]}
GET    testing/runs/                 run history
GET    testing/runs/<id>/?since=N    run status + finished tests from row N on
DELETE testing/runs/<id>/
POST   testing/runs/<id>/stop/
GET    testing/runs/<id>/test/?id=…  one test: data, checks, DB rows, failure, metrics
GET    testing/runs/<id>/log/        pytest output
GET    testing/runs/<id>/screens/<file>   PNG screenshot (data URL in JSON)
"""
import base64

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from app.tenancy.api_super import SuperAdminMixin

from . import runner
from .types import TYPES


def _error(exc, code=status.HTTP_400_BAD_REQUEST):
    return Response({"detail": str(exc)}, status=code)


class TestingHomeView(SuperAdminMixin, APIView):
    def get(self, request):
        runs = runner.list_runs(limit=30)
        latest = {}
        for r in runs:
            if r["status"] != "running":
                latest.setdefault(r["type"], r)
        cat = runner.catalog(cached_only=True)   # never waits for pytest here
        return Response({
            "types": TYPES, "counts": cat["summary"] if cat else None, "catalog_stale": bool(cat and cat["stale"]),
            "active": next((r for r in runs if r["status"] == "running"), None),
            "latest": latest, "runs": runs,
        })


class TestingCasesView(SuperAdminMixin, APIView):
    def get(self, request):
        try:
            return Response(runner.catalog(refresh=request.query_params.get("refresh") == "1"))
        except runner.RunError as exc:
            return _error(exc, status.HTTP_500_INTERNAL_SERVER_ERROR)


class TestingRunsView(SuperAdminMixin, APIView):
    def get(self, request):
        return Response(runner.list_runs(limit=40))

    def post(self, request):
        try:
            meta = runner.start_run(request.data.get("type") or "", request.data.get("tests") or [],
                                    started_by=getattr(request.user, "username", ""))
        except runner.RunError as exc:
            return _error(exc, status.HTTP_409_CONFLICT if "still going" in str(exc) else 400)
        return Response(meta, status=status.HTTP_201_CREATED)


class TestingRunView(SuperAdminMixin, APIView):
    def get(self, request, run_id):
        try:
            since = max(0, int(request.query_params.get("since") or 0))
            return Response(runner.run_detail(run_id, since))
        except (runner.RunError, ValueError) as exc:
            return _error(exc, status.HTTP_404_NOT_FOUND)

    def delete(self, request, run_id):
        try:
            runner.delete_run(run_id)
        except runner.RunError as exc:
            return _error(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TestingStopView(SuperAdminMixin, APIView):
    def post(self, request, run_id):
        try:
            return Response(runner.stop_run(run_id))
        except runner.RunError as exc:
            return _error(exc, status.HTTP_404_NOT_FOUND)


class TestingTestView(SuperAdminMixin, APIView):
    def get(self, request, run_id):
        try:
            return Response(runner.test_detail(run_id, request.query_params.get("id") or ""))
        except runner.RunError as exc:
            return _error(exc, status.HTTP_404_NOT_FOUND)


class TestingLogView(SuperAdminMixin, APIView):
    def get(self, request, run_id):
        try:
            return Response({"log": runner.run_log(run_id)})
        except runner.RunError as exc:
            return _error(exc, status.HTTP_404_NOT_FOUND)


class TestingScreenView(SuperAdminMixin, APIView):
    def get(self, request, run_id, name):
        try:
            path = runner.screenshot(run_id, name)
        except runner.RunError as exc:
            return _error(exc, status.HTTP_404_NOT_FOUND)
        return Response({"data_url": "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()})

"""Console → Testing: run the test suites from the super admin console.

types.py          the seven test types and how a test is sorted into one
pytest_plugin.py  loaded into the pytest child process; records each test's
                  input data, checks (expected / actual), DB writes, metrics
runner.py         starts / stops runs, reads results, regression comparison
api.py            /api/super/testing/... views

Tests always run on SQLite (config.test_settings); the plugin refuses any
other database engine, so a run can never touch the real MySQL data.
"""

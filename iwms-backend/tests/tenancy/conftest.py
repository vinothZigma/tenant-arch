"""Fixtures for tests that create real company databases (SQLite files)."""
import pytest
from django.db import connections
from django.db.backends.base.base import BaseDatabaseWrapper



@pytest.fixture
def tenant_dirs(settings, tmp_path, monkeypatch, django_db_blocker):
    """Company DBs and backups go to a temp dir; company aliases may connect.

    Django's test case only lets a test open connections it declared up
    front, but company DB aliases are registered at runtime. Connections
    whose alias has the company DB prefix are let through; every other alias
    keeps Django's guard.
    """
    from app.tenancy import db as tenant_db

    settings.TENANT_SQLITE_DIR = tmp_path / "tenant_dbs"
    settings.BACKUP_DIR = tmp_path / "backups"
    prefix = settings.TENANT_DB_PREFIX
    guarded = BaseDatabaseWrapper.ensure_connection
    # The unpatched method, saved by pytest-django before it wrapped it.
    real = django_db_blocker._real_ensure_connection

    def ensure_connection(self, *args, **kwargs):
        if self.alias.startswith(prefix):
            return real(self, *args, **kwargs)
        return guarded(self, *args, **kwargs)

    monkeypatch.setattr(BaseDatabaseWrapper, "ensure_connection", ensure_connection)
    yield tmp_path
    for alias in [a for a in connections.databases if a.startswith(prefix)]:
        tenant_db.unregister_tenant_db(alias)

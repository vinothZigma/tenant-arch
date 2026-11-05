"""The company DB the current request (or command, or thread) works in.

A ContextVar instead of a thread-local, so it also behaves under ASGI. New
threads start with no company selected: background jobs must enter
`tenant_context(alias)` themselves for each company they process.
"""
from contextlib import contextmanager
from contextvars import ContextVar

_current_db = ContextVar("iwms_current_tenant_db", default=None)


class TenantNotSelected(RuntimeError):
    """A company table was queried while no company DB was selected."""


def get_current_tenant_db():
    return _current_db.get()


def set_current_tenant_db(alias):
    """Select `alias` and return a token for `reset_current_tenant_db`."""
    return _current_db.set(alias)


def reset_current_tenant_db(token):
    _current_db.reset(token)


@contextmanager
def tenant_context(alias):
    """Run the block against company DB `alias` (a registered connection)."""
    token = _current_db.set(alias)
    try:
        yield alias
    finally:
        _current_db.reset(token)


# Which auth cookie this request uses: (name or None, path). Set by
# TenantMiddleware: /api/t/<slug>/... uses a cookie of its own scoped to that
# path, /api/super/... the super admin's; everything else the portal cookie.
_cookie_scope = ContextVar("iwms_cookie_scope", default=(None, "/"))


def get_cookie_scope():
    return _cookie_scope.get()


def set_cookie_scope(name, path):
    return _cookie_scope.set((name, path))


def reset_cookie_scope(token):
    _cookie_scope.reset(token)


def company_tables_reachable():
    """Can company tables be queried right now? Always in shared mode; in
    per_db mode only with a company selected."""
    from .router import per_db_mode

    return not per_db_mode() or _current_db.get() is not None


def is_platform_payload(payload):
    """A super admin / platform token (never resolved in company tables)."""
    return bool(payload) and (payload.get("scope") == "super" or payload.get("user_type") == "platform")

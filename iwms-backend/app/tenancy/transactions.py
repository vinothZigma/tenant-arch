"""`transaction.atomic()` that also covers the current company DB.

Existing IWMS code calls `transaction.atomic()` without `using`, which
Django opens on the 'default' (master) DB. In per_db mode the company tables
are on another connection, so those blocks would not be atomic for them and
`select_for_update()` on a company table would fail ("select_for_update
cannot be used outside of a transaction").

Installed once from app/__init__.py: a bare `transaction.atomic()` (or
`@transaction.atomic`) with a company selected opens an atomic block on the
company DB *and* on the master DB. An explicit `using=` is left alone, and so
is everything in shared mode.
"""
from contextlib import ContextDecorator

from django.db import DEFAULT_DB_ALIAS, transaction

_original_atomic = transaction.atomic
_OriginalAtomic = transaction.Atomic


def _tenant_alias():
    from .context import get_current_tenant_db
    from .router import per_db_mode

    if not per_db_mode():
        return None
    alias = get_current_tenant_db()
    return alias if alias and alias != DEFAULT_DB_ALIAS else None


class TenantAwareAtomic(ContextDecorator):
    def __init__(self, savepoint=True, durable=False):
        self.savepoint = savepoint
        self.durable = durable
        self._stack = []

    def _recreate_cm(self):
        # A fresh instance per decorated call (recursion-safe).
        return type(self)(self.savepoint, self.durable)

    def __enter__(self):
        aliases = [DEFAULT_DB_ALIAS]
        tenant = _tenant_alias()
        if tenant:
            aliases.append(tenant)
        self._stack = []
        try:
            for alias in aliases:
                cm = _OriginalAtomic(alias, self.savepoint, self.durable)
                cm.__enter__()
                self._stack.append(cm)
        except BaseException:
            self.__exit__(*__import__("sys").exc_info())
            raise

    def __exit__(self, exc_type, exc_value, traceback):
        # Inner (company) block first, then master. If committing one fails,
        # the rest are rolled back.
        suppress = False
        raised = None
        while self._stack:
            cm = self._stack.pop()
            try:
                if cm.__exit__(exc_type, exc_value, traceback):
                    suppress = True
            except BaseException as error:  # e.g. the company DB commit failed
                raised = error
                exc_type, exc_value, traceback = type(error), error, error.__traceback__
        if raised is not None:
            raise raised
        return suppress


def atomic(using=None, savepoint=True, durable=False):
    if callable(using):  # bare @transaction.atomic
        return TenantAwareAtomic(savepoint, durable)(using)
    if using is None:
        return TenantAwareAtomic(savepoint, durable)
    return _original_atomic(using, savepoint, durable)


def install():
    if transaction.atomic is not atomic:
        transaction.atomic = atomic

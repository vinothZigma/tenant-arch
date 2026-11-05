"""Top-level API package."""

# Make bare transaction.atomic() cover the current company DB too (per_db
# mode). Must run before any module decorates functions with it.
from app.tenancy.transactions import install as _install_tenant_atomic

_install_tenant_atomic()

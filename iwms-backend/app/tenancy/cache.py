"""Cache keys carry the current company DB, so one company's cached data is
never served to another (CACHES['default']['KEY_FUNCTION'])."""
from .context import get_current_tenant_db


def make_key(key, key_prefix, version):
    return f"{key_prefix}:{version}:{get_current_tenant_db() or 'master'}:{key}"

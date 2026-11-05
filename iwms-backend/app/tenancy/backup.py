"""Back up a database into .backups/<name>-<timestamp>-<reason>.* before
anything drops a database, table or column, or rewrites data.
"""
import gzip
import os
import re
import shutil
import subprocess

from django.conf import settings
from django.db import connections
from django.utils import timezone

from .db import is_sqlite, sqlite_path
from .router import MASTER_DB
from .slugs import validate_db_name


class BackupFailed(RuntimeError):
    pass


def _target(name, reason, ext):
    settings.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
    reason = re.sub(r"[^a-z0-9-]+", "-", (reason or "manual").lower()).strip("-") or "manual"
    return settings.BACKUP_DIR / f"{name}-{stamp}-{reason}{ext}"


def backup_database(db_name, reason):
    """Dump `db_name` (the master DB's NAME or a company DB) and return the file path."""
    master = connections.databases[MASTER_DB]
    if is_sqlite():
        source = (
            sqlite_path(db_name)
            if db_name != master["NAME"]
            else master["NAME"]
        )
        if not os.path.exists(source):
            raise BackupFailed(f"{source} does not exist")
        target = _target(db_name if db_name != master["NAME"] else "master", reason, ".sqlite3")
        shutil.copy2(source, target)
        return target

    if db_name != master["NAME"]:
        validate_db_name(db_name)
    target = _target(db_name, reason, ".sql.gz")
    cmd = [
        shutil.which("mariadb-dump") or shutil.which("mysqldump") or "mysqldump",
        f"--user={master['USER']}",
        f"--host={master['HOST'] or 'localhost'}",
        f"--port={master['PORT'] or 3306}",
        "--single-transaction", "--routines", "--triggers",
        db_name,
    ]
    env = {**os.environ, "MYSQL_PWD": master["PASSWORD"] or ""}
    proc = subprocess.run(cmd, env=env, capture_output=True)
    if proc.returncode != 0:
        raise BackupFailed(proc.stderr.decode(errors="replace").strip() or "dump failed")
    with gzip.open(target, "wb") as fh:
        fh.write(proc.stdout)
    return target

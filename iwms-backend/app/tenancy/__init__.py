"""Database-per-company multi-tenancy.

    catalog.py  which models live in the master DB vs. each company DB
    context.py  the "current company DB" for this request/thread
    router.py   Django DB router built on the two above
    db.py       register / create / migrate / drop company databases
    slugs.py    company slug and DB-name rules
    services.py provision / deprovision a company (row + database)
    backup.py   dump a database into .backups/ before destructive changes

See helpDoc/14-multi-tenancy.md.
"""

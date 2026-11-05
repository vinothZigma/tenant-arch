"""Tables of the database-per-company system (see helpDoc/14-multi-tenancy.md).

master.py  — in the master DB: plans, modules, per-company module settings,
             plan limits, extra-record purchases, plan-change requests.
tenant.py  — in every company DB: project modules + caps, project admins,
             staff module access, and the registry of dynamic module tables.
"""

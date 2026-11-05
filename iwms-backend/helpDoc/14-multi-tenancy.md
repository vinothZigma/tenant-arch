# 14 — Multi-Tenancy: One Database per Company

Every company has its own MariaDB database. A master database holds what
belongs to the platform: companies, plans, modules, billing and global
lookups. A super admin console manages companies; each company gets a
branded login and a workspace.

```text
 master DB  (DB_NAME, here: iwmsTenant)
 ├── app_company            name, slug, db_name, plan, contact, logo, colour
 ├── tenancy_plan / tenancy_module / tenancy_plan_module_limit
 ├── tenancy_company_module           modules a company uses (+ its own fields)
 ├── tenancy_extra_record_purchase / tenancy_plan_change_request
 ├── auth_user                        platform super admins only
 ├── geography, fuel, vehicle types, app modules, complaint lookups,
 │   scheduler config, common_audit
 └── no-company rows of company tables = the shared catalog new companies copy

 iwms_company_<slug>   (one per company)
 ├── every IWMS company table: staff, projects, vehicles, bins, trips,
 │   complaints, attendance, screen permissions, login audit, ...
 ├── tenancy_project_module   module caps per project (create/edit/delete)
 ├── tenancy_project_admin    which company admins run which project
 ├── staff_access_configuration  staff × project × module permissions
 │                               (+ readable username/project/module/access)
 ├── tenancy_module_table / _column / _lock
 └── module_<code>            one real table per custom module
```

## Quick start

```bash
cp .env.example .env               # fill in DB settings, SECRET_KEY, ...
python manage.py migrate           # master DB
python manage.py seed_defaults     # plans Basic/Medium/High + modules (safe to re-run)
python manage.py createsuperuser   # platform super admin
python manage.py runserver
# frontend: npm run dev  ->  /superadmin/login
```

Existing data in a shared DB (once, per company):

```bash
python manage.py move_company_data blue-planet   # backs up master first
# then TENANCY_MODE=per_db in .env and restart
```

## URLs

| Who | Frontend | API |
|---|---|---|
| Super admin | `/superadmin/login`, `/superadmin/*` | `/api/super/...` |
| Company user | `/<slug>/login`, `/<slug>/*` (workspace) | `/api/t/<slug>/ws/...` |
| Company user, IWMS screens | existing `/admin`, `/dashboard` | `/api/v1/...` (company from the token) or `/api/t/<slug>/<group>/...` |
| Mobile app | — | `/api/v1/...` unchanged (login finds the company by username) |

`TenantMiddleware` (`app/tenancy/middleware.py`) picks the company for every
request: from the `/api/t/<slug>/` prefix (rewritten to `/api/v1/`), from
the token's `company` / `company_unique_id` claim, or — for a super admin
on a legacy URL — from the `X-IWMS-Company` header or a `company_id` query
parameter. A token for one company is refused (401) on another company's URL.

Reserved slugs (never a company): `superadmin, admin, api, static, login,
logout, assets, media, auth, audits, dashboard, district, localbody,
platform, swagger, t`. Duplicates get `-2`, `-3`.

## Roles

| Role | Lives in | What |
|---|---|---|
| Super admin | master `auth_user` (is_superuser) | everything; opens any workspace "as" an admin |
| Company admin | company DB, staff row with `is_company_admin` | the projects assigned to them; their staff's access; extra records; plan requests; branding |
| Staff | company DB, staff row with a login | the project modules their admin granted (never above the project caps) |
| Developer | every company DB, staff row with `is_developer` | admin of every project, hidden, not counted; `DEVELOPER_USERNAME/PASSWORD`, `ensure_developer_users` |

Company users are IWMS staff rows (`StaffcreationOfficeDetails`): the same
login works in the workspace and in the IWMS screens. "Employees" = staff
records; a staff login is created from a staff row in one of the admin's
projects.

## Modules

* **Built-in** (`kind=builtin`): every module of the IWMS permission catalog
  (`app/utils/permission_catalog.py`, 19 modules: Dashboard, Staff
  Management, Transport Masters, Daily Operations, Complaint Management,
  Attendance, Reports, ...), created by `seed_defaults` with the catalog's
  codes, names and routes (`Module.api_paths`). Projects switch them on with
  create/edit/delete caps, enforced on their IWMS routes; staff also need their
  admin's grant there. Modules backed by a table (`MAIN_TABLES` in
  `seed_defaults.py`, e.g. Staff Management -> staff rows) are counted and can
  be limited by plans; the limit applies to the module's main route (its first
  api_path). Others (Dashboard, Reports, Audits, ...) have no records.
  Built-in records are added and edited in the IWMS screens; the workspace
  lists them and refuses edits with a message. Their fields can't be changed.
* **Custom** (`kind=custom`): defined in the console with fields
  (text, textarea, number, email, date, boolean). Each company gets a real
  table `module_<code>`; fields are typed columns. Changing a company's
  fields adds/alters/drops columns (the UI warns how many values a drop
  deletes); a module no longer used by any project has its table dropped.
  Records are validated against the fields and soft-deleted.

## Limits and billing

* Record limit = plan limit + **paid** extra records, per module, all projects
  together; no limit row = unlimited. Counted rows: active and not deleted.
* Creating a record takes a per-module lock row first (`tenancy_module_lock`),
  so two parallel creates can't both pass the limit. Built-in modules: the
  same lock wraps a POST to the module's IWMS route (`PlanLimitMiddleware`),
  plus a `pre_save` check for other paths (bulk upload).
* User limit counts staff with login on (admins included, developer not).
  Switching a login on — in the workspace or the IWMS staff screen — checks it.
* Extra records: company admin requests → pending → super admin marks paid
  (or either cancels). Plan change: company admin requests (one pending at a
  time) → super admin approves (plan switches) or rejects with a reason.

## Writing code for it

* Company tables are only reachable with a company selected. Requests get one
  from the middleware; background work uses
  `app.tenancy.jobs.run_per_company(fn)` or `PerCompanyCommand` (the
  scheduler and the cron commands already do). Outside a company, a company
  query raises `TenantNotSelected` (API: readable 400, logged with a
  traceback).
* `transaction.atomic()` without `using` covers the current company DB and the
  master DB (`app/tenancy/transactions.py`).
* New model: company data unless listed in `app/tenancy/catalog.py`
  (`MASTER_MODELS`). No real ForeignKey between master and company models.
* Data migrations touching company tables: `RunPython(fn, hints={"model_name": "..."})`,
  then `python manage.py migrate_tenants`.
* Cache keys include the company DB (`app/tenancy/cache.py`).

## Commands

| Command | |
|---|---|
| `seed_defaults` | plans + modules (idempotent) |
| `migrate_tenants [--company s] [--create-missing]` | migrate every company DB; one failure doesn't stop the rest |
| `ensure_developer_users` | create/refresh the developer login everywhere |
| `create_company_db "Name"` / `--company s` | bare company + DB (the wizard is the normal way) |
| `drop_company_db s [--delete-company]` | back up and drop |
| `move_company_data s [--replace]` | copy a company's rows from the shared DB into its own |

Anything that drops a database, table or column backs it up first into
`.backups/<db>-<timestamp>-<reason>.sql.gz` (repo root, git-ignored).

## Settings (.env)

| Variable | Default | |
|---|---|---|
| `DB_NAME` | `iwmsTenant` | master DB |
| `TENANCY_MODE` | `shared` | `per_db` = one DB per company (needed for the console/workspace) |
| `TENANT_DB_PREFIX` | `iwms_company_` | company DB names |
| `DEVELOPER_USERNAME` / `DEVELOPER_PASSWORD` | `developer` / empty | no password = no developer login |
| `TENANCY_CURRENCY` | `₹` | price display in limit messages |
| `DB_ENGINE=django.db.backends.sqlite3` | | SQLite fallback: `master.sqlite3` + `tenant_dbs/<db>.sqlite3` |

The DB user needs CREATE/DROP DATABASE (or grants on the master DB and
`iwms_company_%`).

## Known limits

* IWMS screen permissions (screen management / staff access configuration)
  are a separate, existing system. A new company's admin can use the
  workspace at once; to use the IWMS screens, grant them screens as before.
* Cross-company IWMS screens for the super admin (e.g. the old superadmin
  dashboard) need a company chosen (`X-IWMS-Company`); the new console covers
  cross-company views.
* The shared copies of Blue Planet's rows are still in the master DB (kept
  for rollback to `TENANCY_MODE=shared`). Drop them once per_db has run for a
  while — back up first.

## Tests

`tests/tenancy/` (SQLite, company DBs as temp files): router/slug rules,
provisioning, isolation (company A's token on company B → 401), project caps
for staff, the limit under two parallel creates, billing, open workspace,
custom fields, logo checks, data browser. Use the `tenant_dirs` fixture.

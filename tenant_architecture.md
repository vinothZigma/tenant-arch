# Tenant-Based Architecture Documentation

## 1. Overview

This project implements a **multi‑tenant Software‑as‑a‑Service (SaaS)** architecture using Django.  
Each tenant (company) gets **its own database** (or SQLite file) so data is physically isolated.  
The core ideas are:

| Mode | Description |
|------|-------------|
| **shared** (`TENANCY_MODE = "shared"`) | All tenants share the **default** Django database. A `tenant_id` column distinguishes rows. Migrations run once and create tables in `default` for every company. |
| **per_db** (`TENANCY_MODE = "per_db"`) | Each tenant has **its own database** created at runtime. The router (`TenantRouter`) sends every read/write for tenant models to the correct DB. No tables live in `default` after the initial creation phase. |

The code lives primarily in `iwms-backend/app/tenancy/`. The most important files are:

| File | Purpose |
|------|---------|
| `db.py` | Create, register, migrate and **drop** company databases. Contains `create_tenant_db`, `migrate_tenant_db`, `drop_tenant_db`. |
| `context.py` | Manages the **current tenant DB** via a `ContextVar` (`_current_db`). Provides `tenant_context` context manager, `set_current_tenant_db`, `get_current_tenant_db`. |
| `middleware.py` | **TenantMiddleware** extracts the company slug from the URL (`/api/t/<slug>/…`), looks up the `Company` object, and activates the tenant DB with `tenant_context(register_tenant_db(company.db_name))`. |
| `router.py` | Django database router that, in **per_db** mode, routes all tenant models to their own DB. In **shared** mode it returns `None` (default). |
| `data.py` | High‑level helpers for super‑admin to query **every company’s projects, modules and records** (`data_tree`, `company_totals`). Uses `register_tenant_db` to switch connections. |
| `companies.py` (not shown) | Core company model (`Company`) with fields `name`, `slug`, `db_name`, `plan`, etc. |

### How a tenant DB is born

1. **Company creation** – a `Company` record is saved (name, slug, plan, …).  
2. **Database creation** – `create_tenant_db(db_name)` is called:  
   * For PostgreSQL it runs `CREATE DATABASE "db_name"` with the master charset/collation.  
   * For SQLite it creates a `*.sqlite3` file under `TENANT_SQLITE_DIR`.  
3. **Registration** – `register_tenant_db(db_name)` adds the DB alias to Django’s `connections` dict so Django can open it.  
4. **Migrations** – `migrate_tenant_db(db_name)` runs `migrate … run_syncdb=True` inside `tenant_context`, causing **only the company‑specific tables** (defined by the tenant router) to be created.  
5. **Table sync** – `iwms_tables.sync_company` adds any module‑specific columns/tables required by the company’s subscription.

### How a tenant DB is removed

1. **Backup** – the operator must back up the database first (see `backup.py`).  
2. **Unregister** – `unregister_tenant_db(db_name)` closes the connection and removes the alias from `connections`.  
3. **Drop** – `drop_tenant_db(db_name)` executes `DROP DATABASE IF EXISTS "db_name"` (PostgreSQL) or deletes the SQLite file.  
4. **Company record** – the `Company` row can be soft‑deleted (`is_deleted=True`) or permanently removed.

---

## 2. Flowchart

Below is the request‑processing flow for a **per‑DB** tenant. In **shared** mode the middleware still selects a company DB, but Django’s router sends everything to `default`.

```mermaid
flowchart TD
    A[Incoming HTTP Request] --> B{TenantMiddleware}
    B -->|URL matches /api/t/<slug>| C[Extract slug, look up Company]
    B -->|Super admin / legacy| D[Use token or header]
    C --> E{Company exists?}
    E -->|No| F[Return 404/403]
    E -->|Yes| G[company.is_active?]
    G -->|No| F
    G -->|Yes| H[register_tenant_db(company.db_name)]
    H --> I[tenant_context(alias)]
    I --> J[Django Router (per_db mode) / Shared mode]
    J --> K[Query/Write using selected DB alias]
    K --> L[Process view / API]
    L --> M[Return Response]
    M --> N[Exit]
```

**Key points**

* `register_tenant_db` makes the DB alias available (idempotent).  
* `tenant_context` sets the `ContextVar` so the router and `.using()` know which DB to use.  
* All tenant‑specific models are automatically routed to the company’s DB in `per_db` mode.

---

## 3. Comparison: Tenant‑Based vs Shared DB vs Dynamic DB

| Aspect | **Tenant‑Based (per DB)** | **Shared DB (single DB, tenant_id)** | **Dynamic DB (on‑demand creation)** |
|--------|---------------------------|--------------------------------------|--------------------------------------|
| **Isolation** | Full – each company has its own DB (or SQLite file). No cross‑company data leakage. | Logical – rows are separated by `tenant_id`. A faulty query could expose another tenant’s data. | Full – each tenant gets a brand‑new DB, similar to per‑DB mode, but created only when needed. |
| **Performance – loading** | Slightly higher latency on first request (DB registration + connection setup). Subsequent requests hit the dedicated DB directly. | Fastest – single connection, no extra routing. However, tables grow over time, and queries may become slower as data volume increases. | Similar to per‑DB on first create; subsequent load depends on DB size. |
| **Backup & Restore** | Per‑DB backups are independent (e.g., `pg_dump <db_name>`). Restore is isolated; no impact on other tenants. | Single backup file contains **all** tenants. Restore can be risky – a mistake affects everyone. | Same as per‑DB if each tenant DB is backed up separately; otherwise same as shared. |
| **Scalability** | Scale by adding more DB servers or read replicas per tenant. Easy to shard by tenant. | Harder to scale – you must partition or archive old data. Large tables can become a bottleneck. | Scales well if creation is automated; however, managing many small DBs can become operationally heavy. |
| **On‑boarding new tenant** | Requires DB creation + migrations (`create_tenant_db`). Slightly more steps but fully automated via management command. | Just insert a row into the `Company` table and add `tenant_id` to existing tables. Very fast. | Same as per‑DB – automated creation on first use. |
| **Off‑boarding / deletion** | `drop_tenant_db` removes the DB completely. Clean separation. | Must `DELETE` rows or mark `is_deleted`; data remains in the same DB, requiring cleanup scripts. | Same as per‑DB – drop the DB, but must also clean up metadata. |
| **Security** | Strong isolation – compromise of one DB does not expose other tenants’ data. | Weaker – a SQL injection could potentially read another tenant’s rows if `tenant_id` filtering is missed. | Same as per‑DB if each DB is isolated; otherwise same as shared. |
| **Typical use‑case** | SaaS platforms where data privacy is mandatory (healthcare, finance, enterprises). | Early‑stage apps or low‑risk multi‑tenant where a tiny `tenant_id` suffices. | Platforms that need per‑client custom environments (white‑label, regulated industries). |

---

## 4. Scenario Comparison

| Scenario | Tenant‑Based (per DB) | Shared DB | Dynamic DB |
|----------|----------------------|-----------|------------|
| **New tenant onboarding** | Run `create_tenant_db` → DB + tables created in < 30 s. | Add company row, set `tenant_id`. Immediate. | Same as per DB – automated script creates DB on first request. |
| **Daily load (10 k records per tenant)** | Each tenant’s DB stays small; queries are fast. | One large table; indexing on `tenant_id` is essential; may degrade as total rows grow. | Similar to per DB if each tenant DB stays small. |
| **Monthly backup** | `pg_dump –Fc company_db > backup.sql` – independent, can run in parallel. | Single dump of whole DB – larger, longer, must lock tables. | Either per‑DB dumps (if each tenant has its own) or single dump (if shared). |
| **Tenant leaves the service** | `drop_tenant_db` removes the DB instantly. No leftover data in shared DB. | Must soft‑delete company row, purge records, possibly archive. | Same as per DB – drop DB, clean metadata. |
| **Disaster recovery (one tenant corrupted)** | Restore that tenant’s backup without affecting others. | Restore whole DB → all tenants experience downtime. | Restore the specific tenant’s DB backup. |

---

## 5. How DB and Tables Are Created & Deleted

### Creation Flow (high‑level)

1. **Company object saved** (via admin or API).  
2. **Management command** (or API endpoint) calls `create_tenant_db(db_name)` (`db.py:101`):  
   * Validates the name (`validate_db_name`).  
   * For PostgreSQL: `CREATE DATABASE "db_name" CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci`.  
   * For SQLite: creates `company_db.sqlite3` in `TENANT_SQLITE_DIR`.  
3. **Registration** – `register_tenant_db(db_name)` (`db.py:24`) adds the DB config to Django’s `connections`.  
4. **Migration** – `migrate_tenant_db(db_name)` (`db.py:72`):  
   * Runs `migrate ... run_syncdb=True` inside `tenant_context(alias)`.  
   * The **tenant router** ensures only company‑specific tables are created.  
   * After migrate, `iwms_tables.sync_company` adds any module‑specific columns (`db.py:93‑97`).  
5. **Result** – the tenant can now use `/api/t/<slug>/…` and all reads/writes hit its own DB.

### Deletion Flow (high‑level)

1. **Backup** – operator runs a backup (see `backup.py`).  
2. **Unregister** – `unregister_tenant_db(db_name)` (`db.py:40`) closes the connection and removes the alias.  
3. **Drop** – `drop_tenant_db(db_name)` (`db.py:115`):  
   * PostgreSQL: `DROP DATABASE IF EXISTS "db_name"`.  
   * SQLite: `sqlite_path(db_name).unlink(missing_ok=True)`.  
4. **Company record** – set `is_deleted=True` or delete the `Company` row.  
5. **Result** – the DB and all its tables are gone; no residual data remains.

### Key Code References

| Operation | File | Line(s) |
|-----------|------|---------|
| Validate DB name | `slugs.py` (not shown) | – |
| Create DB (PostgreSQL / SQLite) | `db.py:101‑112` | |
| Register DB alias | `db.py:24‑37` | |
| Run migrations inside tenant context | `db.py:72‑98` | |
| Drop DB | `db.py:115‑123` | |
| Set current DB for a request | `middleware.py:179` | |
| Get / reset tenant DB | `context.py:17‑37` | |
| Router decides which DB to use | `router.py:26‑69` | |

---

## 6. Quick Reference Cheat‑Sheet

| Action | Command / Code | File |
|--------|----------------|------|
| **Create a new tenant DB** | `python manage.py create_tenant_db <slug>` (management command wraps `create_tenant_db`) | `db.py` |
| **Migrate tables for a tenant** | `python manage.py migrate_tenant_db <slug>` | `db.py` |
| **Drop a tenant DB** | `python manage.py drop_tenant_db <slug>` (backs up first) | `db.py` |
| **Switch tenant in a shell** | `from tenancy.context import set_current_tenant_db; set_current_tenant_db('mycompany')` | `context.py` |
| **Route a queryset to a tenant DB** | `MyModel.objects.using(company.db_name).all()` | router / `data.py` |
| **List all tenant DBs** | `from tenancy.db import tenant_table_names; tenant_table_names('company_slug')` | `db.py` |

---

*End of documentation.*
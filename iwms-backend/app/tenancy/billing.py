"""Plans, record/user limits, extra records and plan-change requests.

Record limit = plan limit + paid extra records, across all projects:
per sub-module for built-in IWMS modules (PlanScreenLimit, e.g. Transport
Masters > Vehicle Creation) and per module for custom modules
(PlanModuleLimit). No limit row = unlimited. The user limit counts billable
logins: staff with login on (company admins included), never the developer.
"""
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from app.models.superadmin.staff_management.staffcreation import Staffcreation
from app.models.superadmin_masters.company import Company
from app.models.tenancy.master import ExtraRecordPurchase, PlanChangeRequest
from app.models.tenancy.master import CompanyModule, Module

from .db import register_tenant_db
from .modules import ensure_lock_row, lock_and_count, module_totals
from .sub_modules import module_sub_modules, screen_label, screen_parts, screen_qs

CURRENCY = getattr(settings, "TENANCY_CURRENCY", "₹")


def billable_users(alias):
    """Logins that count towards the plan's user limit."""
    return Staffcreation.objects.using(alias).filter(
        is_deleted=False, is_developer=False, login_enabled=True
    )


USERS_CODE = "users"            # purchase target for extra user slots (module empty)
USERS_LABEL = "User logins"


def extra_users(company, status=None):
    """User slots bought (paid by default) — purchases with no module."""
    rows = ExtraRecordPurchase.objects.filter(
        company=company, module__isnull=True, status=status or ExtraRecordPurchase.PAID)
    return rows.aggregate(q=Sum("quantity"))["q"] or 0


def user_limit_for(company):
    """Plan users + paid extra user slots; None = no plan (no limit)."""
    if not company.plan:
        return None
    return company.plan.user_limit + extra_users(company)


def company_modules(company):
    return [cm.module for cm in CompanyModule.objects.filter(company=company).select_related("module")]


def module_quotas(company, counts=None):
    """{code: {module_id, module_name, kind, used, base_limit, extra, limit, extra_price, pending}}
    for the company's modules. limit None = unlimited."""
    cms = list(CompanyModule.objects.filter(company=company).select_related("module"))
    if counts is None:
        counts = module_totals(register_tenant_db(company.db_name), [cm.module for cm in cms])
    plan_limits = {pl.module_id: pl for pl in company.plan.module_limits.all()} if company.plan else {}

    def extras(status):
        rows = (ExtraRecordPurchase.objects.filter(company=company, status=status, module__isnull=False)
                .values("module_id").annotate(q=Sum("quantity")))
        return {r["module_id"]: r["q"] for r in rows}

    paid, pending = extras(ExtraRecordPurchase.PAID), extras(ExtraRecordPurchase.PENDING)
    quotas = {}
    for cm in cms:
        m, pl = cm.module, plan_limits.get(cm.module_id)
        base = pl.record_limit if pl else None
        extra = paid.get(m.id, 0)
        quotas[m.code] = {
            "module_id": m.id, "module_name": m.name, "kind": m.kind,
            "used": counts.get(m.code, 0),
            "base_limit": base, "extra": extra,
            "limit": None if base is None else base + extra,
            "extra_price": float(pl.extra_record_price) if pl else 0.0,
            "pending": pending.get(m.id, 0),
        }
    return quotas


def screen_quotas(company, counts=None, only=None):
    """{screen: {module_code, module_id, module_name, screen, label, used,
    base_limit, extra, limit, extra_price, pending}} for every sub-module (with
    records) of the company's built-in modules. limit None = unlimited.
    counts={} skips counting (used = 0); `only` limits it to some screens."""
    cms = [cm for cm in CompanyModule.objects.filter(company=company).select_related("module")
           if cm.module.kind == Module.BUILTIN]
    limits = {sl.screen: sl for sl in company.plan.screen_limits.all()} if company.plan else {}

    def extras(status):
        rows = (ExtraRecordPurchase.objects.filter(company=company, status=status).exclude(screen="")
                .values("screen").annotate(q=Sum("quantity")))
        return {r["screen"]: r["q"] for r in rows}

    paid, pending = extras(ExtraRecordPurchase.PAID), extras(ExtraRecordPurchase.PENDING)
    alias = register_tenant_db(company.db_name) if counts is None else None
    out = {}
    for cm in sorted(cms, key=lambda c: c.module.name):
        m = cm.module
        for sub in module_sub_modules(m.code):
            name = sub["screen"]
            if only is not None and name not in only:
                continue
            if counts is None:
                try:
                    used = screen_qs(alias, name).count()
                except Exception:   # table dropped for every project (Edit project > Modules)
                    used = 0
            else:
                used = counts.get(name, 0)
            sl = limits.get(name)
            base = sl.record_limit if sl else None
            extra = paid.get(name, 0)
            out[name] = {
                "module_code": m.code, "module_id": m.id, "module_name": m.name,
                "screen": name, "label": sub["label"], "parts": list(sub["parts"]),
                "used": used, "base_limit": base, "extra": extra,
                "limit": None if base is None else base + extra,
                "extra_price": float(sl.extra_record_price) if sl else 0.0,
                "pending": pending.get(name, 0),
            }
    return out


def screen_room(company, alias, screen, *, is_admin=True, adding=1):
    """Raise PermissionDenied unless `adding` more counted rows fit the
    sub-module's limit (plan + paid extra)."""
    quota = screen_quotas(company, counts={}, only={screen}).get(screen)
    if not quota or quota["limit"] is None:
        return
    used = screen_qs(alias, screen).count()
    if used + adding > quota["limit"]:
        raise PermissionDenied(limit_message(company, {**quota, "used": used}, is_admin))


def company_usage(company):
    alias = register_tenant_db(company.db_name)
    return {
        "plan": company.plan.name if company.plan else None,
        "users_used": billable_users(alias).count(),
        "user_limit": user_limit_for(company),
        "user_base_limit": company.plan.user_limit if company.plan else None,
        "user_extra": extra_users(company),
        "user_pending": extra_users(company, ExtraRecordPurchase.PENDING),
        "extra_user_price": float(company.plan.extra_user_price) if company.plan else 0.0,
        # Custom modules (built-in modules have no module-level limit any more).
        "modules": module_quotas(company),
        # Built-in modules, per sub-module.
        "sub_modules": screen_quotas(company),
    }


def limit_message(company, quota, is_admin):
    name = f'{quota["module_name"]} › {quota["label"]}' if quota.get("label") else quota["module_name"]
    if not is_admin:
        return f"{name} is full for now. Ask your company admin to add more."
    extra = f' + {quota["extra"]} extra' if quota["extra"] else ""
    price = (f' Extra records cost {CURRENCY}{quota["extra_price"]:g} each.'
             if quota["extra_price"] else "")
    return (f'{name} limit reached: {quota["base_limit"]} on the {company.plan.name} plan{extra} '
            f"(all projects together).{price} Your company admin can buy more.")


def record_room(company, alias, module, *, is_admin=True, adding=1):
    """Inside transaction.atomic(using=alias): lock the module and raise
    PermissionDenied unless `adding` more counted rows fit."""
    quota = module_quotas(company, counts={}).get(module.code)
    if not quota or quota["limit"] is None:
        return
    used = lock_and_count(alias, module)
    if used + adding > quota["limit"]:
        raise PermissionDenied(limit_message(company, {**quota, "used": used}, is_admin))


def guarded_create(company, alias, module, create, *, is_admin=True):
    """Run create() so that two parallel creates can't both pass the limit."""
    ensure_lock_row(alias, module.code)
    with transaction.atomic(using=alias):
        record_room(company, alias, module, is_admin=is_admin)
        return create()


def user_room(company, alias, adding=1, what="users"):
    """Raise PermissionDenied unless `adding` more billable logins fit the plan."""
    if not company.plan:
        return
    used = billable_users(alias).count()
    limit = user_limit_for(company)
    if used + adding > limit:
        extra = extra_users(company)
        allows = f"{company.plan.user_limit} + {extra} extra" if extra else str(limit)
        raise PermissionDenied(
            f"User limit reached: your {company.plan.name} plan allows {allows} "
            f"{what} with login switched on ({used} in use). Switch someone else off first, "
            "buy extra user slots or upgrade the plan (Company profile).")


def request_extra_records(company, module, quantity, requested_by, status=None, unit_price=None, note="",
                          screen=""):
    """module=None: extra user slots. screen: extra records of that sub-module."""
    if unit_price is None:
        if module is None:
            unit_price = company.plan.extra_user_price if company.plan else Decimal("0")
        elif screen:
            sl = company.plan.screen_limits.filter(screen=screen).first() if company.plan else None
            unit_price = sl.extra_record_price if sl else Decimal("0")
        else:
            pl = company.plan.module_limits.filter(module=module).first() if company.plan else None
            unit_price = pl.extra_record_price if pl else Decimal("0")
    unit_price = Decimal(str(unit_price))
    status = status or ExtraRecordPurchase.PENDING
    return ExtraRecordPurchase.objects.create(
        company=company, module=module, screen=screen or "", quantity=quantity, unit_price=unit_price,
        total_amount=unit_price * quantity, status=status, requested_by=requested_by, note=note,
        paid_at=timezone.now() if status == ExtraRecordPurchase.PAID else None,
    )


def decide_plan_request(req, approve, note=""):
    with transaction.atomic():
        req = PlanChangeRequest.objects.select_for_update().get(pk=req.pk)
        if req.status != PlanChangeRequest.PENDING:
            raise ValueError(f"This request is already {req.status}.")
        req.status = PlanChangeRequest.APPROVED if approve else PlanChangeRequest.REJECTED
        req.note = (note or "")[:255]
        req.decided_at = timezone.now()
        req.save(update_fields=["status", "note", "decided_at"])
        if approve:
            Company.objects.filter(pk=req.company_id).update(plan=req.to_plan)
    return req


def plan_json(plan, module_ids=None):
    limits = {pl.module_id: pl for pl in plan.module_limits.select_related("module")}
    modules = [
        {"module_id": mid, "module_name": pl.module.name, "record_limit": pl.record_limit,
         "extra_record_price": float(pl.extra_record_price)}
        for mid, pl in limits.items() if module_ids is None or mid in module_ids
    ]
    modules.sort(key=lambda m: m["module_name"])
    sub_modules = [
        {"module_id": sl.module_id, "module_code": sl.module.code, "module_name": sl.module.name,
         "screen": sl.screen, "label": screen_label(sl.screen), "parts": list(screen_parts(sl.screen)),
         "record_limit": sl.record_limit,
         "extra_record_price": float(sl.extra_record_price)}
        for sl in plan.screen_limits.select_related("module")
        if module_ids is None or sl.module_id in module_ids
    ]
    sub_modules.sort(key=lambda s: (s["module_name"], s["label"]))
    return {"id": plan.id, "code": plan.code, "name": plan.name, "description": plan.description,
            "price": float(plan.price), "user_limit": plan.user_limit,
            "extra_user_price": float(plan.extra_user_price), "modules": modules,
            "sub_modules": sub_modules}

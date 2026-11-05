"""Plans, record/user limits, extra records and plan-change requests.

Record limit = plan limit + paid extra records, per module, across all
projects. No PlanModuleLimit row = unlimited. The user limit counts billable
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
from app.models.tenancy.master import CompanyModule

from .db import register_tenant_db
from .modules import ensure_lock_row, lock_and_count, module_totals

CURRENCY = getattr(settings, "TENANCY_CURRENCY", "₹")


def billable_users(alias):
    """Logins that count towards the plan's user limit."""
    return Staffcreation.objects.using(alias).filter(
        is_deleted=False, is_developer=False, login_enabled=True
    )


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
        rows = (ExtraRecordPurchase.objects.filter(company=company, status=status)
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


def company_usage(company):
    alias = register_tenant_db(company.db_name)
    return {
        "plan": company.plan.name if company.plan else None,
        "users_used": billable_users(alias).count(),
        "user_limit": company.plan.user_limit if company.plan else None,
        "modules": module_quotas(company),
    }


def limit_message(company, quota, is_admin):
    name = quota["module_name"]
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
    if used + adding > company.plan.user_limit:
        raise PermissionDenied(
            f"User limit reached: your {company.plan.name} plan allows {company.plan.user_limit} "
            f"{what} with login switched on ({used} in use). Switch someone else off first, "
            "or upgrade the plan.")


def request_extra_records(company, module, quantity, requested_by, status=None, unit_price=None, note=""):
    if unit_price is None:
        pl = company.plan.module_limits.filter(module=module).first() if company.plan else None
        unit_price = pl.extra_record_price if pl else Decimal("0")
    unit_price = Decimal(str(unit_price))
    status = status or ExtraRecordPurchase.PENDING
    return ExtraRecordPurchase.objects.create(
        company=company, module=module, quantity=quantity, unit_price=unit_price,
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
    return {"id": plan.id, "code": plan.code, "name": plan.name, "description": plan.description,
            "price": float(plan.price), "user_limit": plan.user_limit, "modules": modules}

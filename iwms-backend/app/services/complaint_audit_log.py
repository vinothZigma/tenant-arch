"""Fill Complaint Audit's own table (ComplaintAuditLog / complaint_audit_logs).

Every row the complaint workflow writes to a history table (status change,
reopen, escalation, reassignment, feedback) and every ticket created or
deleted is recorded here as it happens (post_save signals, connected in
ApiConfig.ready). backfill() records what happened before the table existed
(run when the table is created, app/tenancy/iwms_tables.py).

Logging never breaks the complaint workflow: a failure is logged and the
save goes on. A company that turned Complaint Audit off has no table; writes
to it are skipped (app/tenancy/orm_columns.py).
"""
import logging

from django.db import connections
from django.db.models.signals import post_save
from django.utils import timezone

logger = logging.getLogger(__name__)


def _actor(row, prefix):
    for kind in ("staff", "user", "customer"):
        value = getattr(row, f"{prefix}_{kind}_id", None)
        if value:
            return kind, str(value)
    if getattr(row, f"{prefix}_system", False):
        return "system", None
    return "", None


def _events(row):
    """[(event, fields)] that `row` means for the audit."""
    from app.models.core_modules.complaint_management.transactions import (
        ComplaintAssignmentHistory, ComplaintEscalationHistory, ComplaintFeedback,
        ComplaintReopenHistory, ComplaintStatusHistory,
    )
    from app.models.core_modules.complaint_management.ticket import ComplaintTicket
    from app.models.superadmin.audits.complaint_audit import ComplaintAuditLog as L

    if isinstance(row, ComplaintStatusHistory):
        kind, who = _actor(row, "changed_by")
        return [(L.STATUS, dict(ticket_id=row.ticket_id, from_status_id=row.from_status_id,
                                to_status_id=row.to_status_id, actor_type=kind, actor_id=who,
                                detail=row.remarks or "", happened_at=row.changed_at,
                                extra={"visible_to_citizen": bool(row.visible_to_citizen)}))]
    if isinstance(row, ComplaintReopenHistory):
        kind, who = _actor(row, "reopened_by")
        return [(L.REOPENED, dict(ticket_id=row.ticket_id, from_status_id=row.previous_status_id,
                                  actor_type=kind, actor_id=who, detail=row.reopen_reason or "",
                                  happened_at=row.reopened_at))]
    if isinstance(row, ComplaintEscalationHistory):
        kind, who = _actor(row, "escalated_by")
        return [(L.ESCALATED, dict(ticket_id=row.ticket_id, actor_type=kind, actor_id=who,
                                   detail=row.reason or "", happened_at=row.escalated_at,
                                   extra={"level": row.escalation_level,
                                          "to_staff_id": row.escalated_to_staff_id,
                                          "to_user_id": row.escalated_to_user_id}))]
    if isinstance(row, ComplaintAssignmentHistory):
        return [(L.REASSIGNED, dict(ticket_id=row.ticket_id, actor_type="user" if row.assigned_by_id else "",
                                    actor_id=row.assigned_by_id, detail=row.assignment_reason or "",
                                    happened_at=row.assigned_at,
                                    extra={"from_staff_id": row.from_staff_id, "to_staff_id": row.to_staff_id,
                                           "from_user_id": row.from_user_id, "to_user_id": row.to_user_id}))]
    if isinstance(row, ComplaintFeedback):
        return [(L.FEEDBACK, dict(ticket_id=row.ticket_id, actor_type="customer", actor_id=row.customer_id,
                                  detail=row.feedback_text or "", happened_at=row.submitted_at,
                                  extra={"rating": row.rating, "is_issue_solved": row.is_issue_solved}))]
    if isinstance(row, ComplaintTicket):
        out = [(L.CREATED, dict(ticket_id=row.unique_id, to_status_id=row.status_id,
                                happened_at=row.created_at or timezone.now()))]
        if row.is_deleted:
            out.append((L.DELETED, dict(ticket_id=row.unique_id, detail=row.delete_reason or "",
                                        actor_id=row.deleted_by_id, actor_type="staff" if row.deleted_by_id else "",
                                        happened_at=row.deleted_at or timezone.now())))
        return out
    return []


def record(row, using=None):
    """Write the audit rows `row` means (already recorded ones are skipped)."""
    from app.models.core_modules.complaint_management.ticket import ComplaintTicket
    from app.models.superadmin.audits.complaint_audit import ComplaintAuditLog

    using = using or row._state.db
    events = _events(row)
    if not events:
        return 0
    tickets = ComplaintTicket._base_manager.using(using)
    ticket = row if isinstance(row, ComplaintTicket) else tickets.filter(unique_id=events[0][1]["ticket_id"]).first()
    n = 0
    for event, fields in events:
        fields["happened_at"] = fields.get("happened_at") or timezone.now()
        _obj, made = ComplaintAuditLog.objects.using(using).get_or_create(
            source_table=row._meta.db_table, source_id=str(row.unique_id or row.pk), event=event,
            defaults={**fields, "ticket_no": getattr(ticket, "ticket_no", "") or "",
                      "company_id": getattr(ticket, "company_id", None),
                      "project_id": getattr(ticket, "project_id", None)})
        n += int(made)
    return n


def _on_save(sender, instance, created, raw=False, using=None, **kwargs):
    from app.models.core_modules.complaint_management.ticket import ComplaintTicket

    if raw or (not created and not (isinstance(instance, ComplaintTicket) and instance.is_deleted)):
        return
    try:
        record(instance, using)
    except Exception:
        logger.exception("Could not write the complaint audit log for %s", instance)


def connect_signals():
    from app.models.core_modules.complaint_management.ticket import ComplaintTicket
    from app.models.core_modules.complaint_management.transactions import (
        ComplaintAssignmentHistory, ComplaintEscalationHistory, ComplaintFeedback,
        ComplaintReopenHistory, ComplaintStatusHistory,
    )

    for model in (ComplaintTicket, ComplaintStatusHistory, ComplaintReopenHistory, ComplaintEscalationHistory,
                  ComplaintAssignmentHistory, ComplaintFeedback):
        post_save.connect(_on_save, sender=model, dispatch_uid=f"complaint_audit_log.{model.__name__}")


def backfill(alias):
    """Record every existing ticket/history row (safe to run again)."""
    from app.models.core_modules.complaint_management.ticket import ComplaintTicket
    from app.models.core_modules.complaint_management.transactions import (
        ComplaintAssignmentHistory, ComplaintEscalationHistory, ComplaintFeedback,
        ComplaintReopenHistory, ComplaintStatusHistory,
    )

    have = set(connections[alias].introspection.table_names())
    n = 0
    for model in (ComplaintTicket, ComplaintStatusHistory, ComplaintReopenHistory, ComplaintEscalationHistory,
                  ComplaintAssignmentHistory, ComplaintFeedback):
        if model._meta.db_table not in have:
            continue
        for row in model._base_manager.using(alias).iterator(chunk_size=500):
            n += record(row, alias)
    return n

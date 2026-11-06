from django.db import models


class ComplaintAuditLog(models.Model):
    """Complaint Audit's own table: one row per complaint ticket event, written
    as the ticket workflow records it (app/services/complaint_audit_log.py).
    `source_table` + `source_id` point at the row that caused it, so recording
    the same event twice is impossible."""

    CREATED, STATUS, REOPENED, ESCALATED, REASSIGNED, FEEDBACK, DELETED = (
        "created", "status_changed", "reopened", "escalated", "reassigned", "feedback", "deleted")
    EVENTS = [(e, e.replace("_", " ").capitalize()) for e in
              (CREATED, STATUS, REOPENED, ESCALATED, REASSIGNED, FEEDBACK, DELETED)]

    ticket_id = models.CharField(max_length=100, db_index=True)
    ticket_no = models.CharField(max_length=100, blank=True, default="")
    company_id = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    project_id = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    event = models.CharField(max_length=30, choices=EVENTS)
    from_status_id = models.CharField(max_length=100, null=True, blank=True)
    to_status_id = models.CharField(max_length=100, null=True, blank=True)
    actor_type = models.CharField(max_length=20, blank=True, default="")   # staff / user / customer / system
    actor_id = models.CharField(max_length=100, null=True, blank=True)
    detail = models.TextField(blank=True, default="")                       # remarks / reason / feedback text
    extra = models.JSONField(default=dict, blank=True)                      # level, rating, assignee...
    happened_at = models.DateTimeField(db_index=True)
    source_table = models.CharField(max_length=64)
    source_id = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "complaint_audit_logs"
        ordering = ["happened_at", "pk"]
        unique_together = [("source_table", "source_id", "event")]

    def __str__(self):
        return f"{self.ticket_no or self.ticket_id} {self.event} @ {self.happened_at:%Y-%m-%d %H:%M}"

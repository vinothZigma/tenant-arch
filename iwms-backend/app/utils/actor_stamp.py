"""Stamp created_by_id / updated_by_id from the request user.

For plain ModelViewSets (e.g. Company, Project) that don't go through
CompanyScopedViewSet, which does its own stamping. The id stored is the
same one CompanyScopedViewSet stores (Account.account_id = the user's
unique_id / the staff's staff_unique_id), via resolve_actor().

Put it after AuditViewSetMixin in the bases so the audit log still wraps
the save:  class X(AuditViewSetMixin, ActorStampMixin, viewsets.ModelViewSet)
"""
from app.utils.audit_context import resolve_actor


class ActorStampMixin:
    def _actor_id(self):
        actor_id, _, _ = resolve_actor(getattr(self.request, "user", None))
        return actor_id

    def perform_create(self, serializer):
        actor_id = self._actor_id()
        if actor_id is None:
            return super().perform_create(serializer)
        serializer.save(created_by_id=actor_id, updated_by_id=actor_id)

    def perform_update(self, serializer):
        actor_id = self._actor_id()
        if actor_id is None:
            return super().perform_update(serializer)
        serializer.save(updated_by_id=actor_id)

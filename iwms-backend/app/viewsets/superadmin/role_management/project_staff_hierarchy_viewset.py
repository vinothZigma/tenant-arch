from rest_framework import viewsets

from app.models.superadmin.role_management.projectStaffHierarchy import ProjectStaffHierarchy
from app.serializers.superadmin.role_management.project_staff_hierarchy_serializer import (
    ProjectStaffHierarchySerializer,
)
from app.utils.audit_mixin import AuditViewSetMixin
from app.utils.filters import ModelFieldQueryFilter


class ProjectStaffHierarchyViewSet(AuditViewSetMixin, viewsets.ModelViewSet):
    pagination_class = None
    queryset = ProjectStaffHierarchy.objects.filter(is_deleted=False)
    serializer_class = ProjectStaffHierarchySerializer
    lookup_field = "unique_id"
    filter_backends = [ModelFieldQueryFilter]

    AUDIT_MODULE = "role-assigns"
    AUDIT_ENDPOINT = "project-staff-hierarchy"

    permission_resource = "ProjectStaffHierarchy"

    def get_queryset(self):
        """Rows of the projects the caller may see, narrowed by ?company_id /
        ?project_id (the list's Company & project filter)."""
        from app.models.superadmin_masters.project import Project
        from app.viewsets.superadmin_masters.project_management import visible_project_ids

        queryset = ProjectStaffHierarchy.objects.filter(is_deleted=False)
        params = self.request.query_params
        company_id = params.get("company_id") or params.get("company_unique_id")
        project_id = params.get("project_id") or params.get("project_unique_id")

        # Same project scope as the Project screen: a super admin sees every
        # company, a company login only its company's projects it may open.
        allowed = visible_project_ids(self.request)
        if company_id:
            in_company = list(Project.objects.filter(company_id=company_id, is_deleted=False)
                              .values_list("unique_id", flat=True))
            allowed = in_company if allowed is None else [p for p in allowed if p in set(in_company)]
        if allowed is not None:
            queryset = queryset.filter(project_id__in=allowed)
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        return queryset

    def perform_destroy(self, instance):
        instance.delete()

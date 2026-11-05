"""URLs of the super admin console API and the company workspace API."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import api_company as company
from . import api_super as sup

router = DefaultRouter(trailing_slash=True)
router.include_root_view = False
router.register("plans", sup.PlanViewSet, basename="tenancy-plans")
router.register("modules", sup.ModuleViewSet, basename="tenancy-modules")
router.register("companies", sup.CompanyViewSet, basename="tenancy-companies")
router.register("extra-records", sup.ExtraRecordPurchaseViewSet, basename="tenancy-extra-records")
router.register("plan-requests", sup.PlanChangeRequestViewSet, basename="tenancy-plan-requests")

DATA = "data/<str:company_id>/<str:project_id>/<slug:module_code>/"

# /api/super/...
super_patterns = [
    path("login/", sup.LoginView.as_view()),
    path("logout/", sup.LogoutView.as_view()),
    path("me/", sup.MeView.as_view()),
    path("me/password/", sup.PasswordView.as_view()),
    path("dashboard/", sup.DashboardView.as_view()),
    path("data/", sup.DataTreeView.as_view()),
    path(DATA, sup.DataRecordsView.as_view()),
    path(DATA + "<str:pk>/", sup.DataRecordDetailView.as_view()),
    path(DATA + "<str:pk>/restore/", sup.DataRecordRestoreView.as_view()),
    path("", include(router.urls)),
]

# /api/t/<slug>/ws/... (TenantMiddleware rewrites it to /api/v1/ws/...)
RECORDS = "projects/<str:project_id>/modules/<slug:module_code>/records/"
workspace_patterns = [
    path("info/", company.CompanyInfoView.as_view()),
    path("auth/login/", company.LoginView.as_view()),
    path("auth/logout/", company.LogoutView.as_view()),
    path("me/", company.MeView.as_view()),
    path("me/profile/", company.ProfileView.as_view()),
    path("me/password/", company.PasswordView.as_view()),
    path(RECORDS, company.RecordListView.as_view()),
    path(RECORDS + "<int:pk>/", company.RecordDetailView.as_view()),
    path("users/", company.UserListView.as_view()),
    path("users/<str:pk>/", company.UserDetailView.as_view()),
    path("staff-directory/", company.StaffDirectoryView.as_view()),
    path("extra-records/", company.ExtraRecordListView.as_view()),
    path("extra-records/<int:pk>/cancel/", company.ExtraRecordCancelView.as_view()),
    path("plans/", company.PlanOptionsView.as_view()),
    path("plan-requests/", company.PlanRequestView.as_view()),
    path("plan-requests/<int:pk>/cancel/", company.PlanRequestCancelView.as_view()),
    path("form-config/", company.FormConfigView.as_view()),
    path("extras/<str:form>/<str:record_id>/", company.ExtraValuesView.as_view()),
    path("company/logo/", company.CompanyLogoView.as_view()),
    path("company/branding/", company.CompanyBrandingView.as_view()),
]

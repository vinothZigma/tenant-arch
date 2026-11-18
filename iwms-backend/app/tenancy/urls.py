"""URLs of the super admin console API and the company workspace API."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import api_company as company
from . import api_super as sup
from app.testing import api as testing

router = DefaultRouter(trailing_slash=True)
router.include_root_view = False
router.register("plans", sup.PlanViewSet, basename="tenancy-plans")
router.register("modules", sup.ModuleViewSet, basename="tenancy-modules")
router.register("companies", sup.CompanyViewSet, basename="tenancy-companies")
router.register("extra-records", sup.ExtraRecordPurchaseViewSet, basename="tenancy-extra-records")
router.register("plan-requests", sup.PlanChangeRequestViewSet, basename="tenancy-plan-requests")
router.register("print-templates", sup.PrintTemplateViewSet, basename="tenancy-print-templates")

# /api/super/...
super_patterns = [
    path("login/", sup.LoginView.as_view()),
    path("logout/", sup.LogoutView.as_view()),
    path("me/", sup.MeView.as_view()),
    path("me/password/", sup.PasswordView.as_view()),
    path("dashboard/", sup.DashboardView.as_view()),
    path("excel-screens/", sup.ExcelScreensView.as_view()),
    # Console → Testing (app/testing/)
    path("testing/", testing.TestingHomeView.as_view()),
    path("testing/cases/", testing.TestingCasesView.as_view()),
    path("testing/runs/", testing.TestingRunsView.as_view()),
    path("testing/runs/<str:run_id>/", testing.TestingRunView.as_view()),
    path("testing/runs/<str:run_id>/stop/", testing.TestingStopView.as_view()),
    path("testing/runs/<str:run_id>/test/", testing.TestingTestView.as_view()),
    path("testing/runs/<str:run_id>/log/", testing.TestingLogView.as_view()),
    path("testing/runs/<str:run_id>/screens/<str:name>/", testing.TestingScreenView.as_view()),
    # No "data/" browser: the platform super admin doesn't read company
    # records; the company's developer login sees everything instead.
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
    path("print-template/<slug:report_code>/", company.PrintTemplateView.as_view()),
    path("excel-columns/", company.ExcelColumnsView.as_view()),
]

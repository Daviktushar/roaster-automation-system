from django.contrib.auth import views as auth_views
from django.urls import path

from planner import views

# Keep workflow route names in this module aligned with links in planner templates.
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("accounts/login/", views.login_view, name="login"),
    path("accounts/logout/", views.logout_view, name="logout"),
    path("accounts/signup/", views.signup_view, name="signup"),
    path("accounts/forgot-password/", auth_views.PasswordResetView.as_view(template_name="planner/forgot_password.html", email_template_name="planner/password_reset_email.txt", success_url="/accounts/forgot-password/sent/"), name="password_reset"),
    path("accounts/forgot-password/sent/", auth_views.PasswordResetDoneView.as_view(template_name="planner/password_reset_done.html"), name="password_reset_done"),
    path("accounts/reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(template_name="planner/password_reset_confirm.html", success_url="/accounts/reset/complete/"), name="password_reset_confirm"),
    path("accounts/reset/complete/", auth_views.PasswordResetCompleteView.as_view(template_name="planner/password_reset_complete.html"), name="password_reset_complete"),
    path("workbooks/upload/", views.upload_workbook, name="upload_workbook"),
    path("workbooks/<int:workbook_id>/", views.workbook_detail, name="workbook_detail"),
    path("workbooks/<int:workbook_id>/staffing/", views.configure_staffing, name="configure_staffing"),
    path("workbooks/<int:workbook_id>/history/import/", views.import_roster_history, name="import_roster_history"),
    path("workbooks/<int:workbook_id>/history/baseline/", views.confirm_pattern_baseline, name="confirm_pattern_baseline"),
    path("workbooks/<int:workbook_id>/employees/<int:employee_id>/edit/", views.edit_employee, name="edit_employee"),
    path("workbooks/<int:workbook_id>/employees/<int:employee_id>/roster/", views.toggle_employee_roster, name="toggle_employee_roster"),
    path("rosters/generate/", views.generate_roster, name="generate_roster"),
    path("rosters/<int:run_id>/", views.run_detail, name="run_detail"),
    path("rosters/<int:run_id>/approve/", views.approve_run, name="approve_run"),
    path("rosters/<int:run_id>/download/", views.download_run, name="download_run"),
]

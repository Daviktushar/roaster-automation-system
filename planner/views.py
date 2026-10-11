import calendar
import logging
from datetime import date, timedelta
from pathlib import Path

from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.db import transaction
from django.db.models import Q

from planner.forms import EmployeeDetailsForm, GenerationForm, PatternBaselineForm, SignUpForm, StaffingConfigurationForm, WorkbookUploadForm
from planner.models import Employee, EmployeeAssignment, EmployeePatternChange, GenerationRun, HistoricalRosterSnapshot, PatternHistoryBaseline, RosterSheet, StaffingRequirement, WorkbookImport
from planner.services.excel_export import export_consolidated_workbook, export_workbook
from planner.services.history import history_status, import_workbook_history
from planner.services.roster_generation import (
    _fixed_coverage_target,
    _inferred_coverage_target,
    _month_days,
    _previous_shift,
    _required_shifts,
    create_roster_preview,
)
from planner.services.workbook_import import WorkbookImportError, parse_workbook, save_import_records


logger = logging.getLogger(__name__)


def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    error = ""
    if request.method == "POST":
        identifier = request.POST.get("identifier", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=identifier, password=password)
        if user is None and "@" in identifier:
            matches = get_user_model().objects.filter(email__iexact=identifier)
            if matches.count() == 1:
                user = authenticate(request, username=matches.first().get_username(), password=password)
        if user:
            login(request, user)
            return redirect(request.GET.get("next") or "dashboard")
        error = "Username or password was not recognized."
    return render(request, "planner/login.html", {"error": error})


def logout_view(request):
    if request.method == "POST":
        logout(request)
    return redirect("login")


def signup_view(request):
    form = SignUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save(commit=False)
        user.email = form.cleaned_data["email"]
        user.first_name = form.cleaned_data["first_name"]
        user.save()
        login(request, user)
        messages.success(request, "Your workspace account is ready.")
        return redirect("dashboard")
    return render(request, "planner/signup.html", {"form": form})


@login_required
def dashboard(request):
    return render(request, "planner/dashboard.html", _dashboard_context(request))


def _dashboard_context(request, upload_form=None):
    imports = WorkbookImport.objects.filter(uploaded_by=request.user).order_by("-uploaded_at")
    latest = imports.first()
    runs = GenerationRun.objects.filter(created_by=request.user).select_related("source")[:6]
    form = GenerationForm(workbook_import=latest) if latest else None
    return {
        "imports": imports[:6], "latest": latest, "runs": runs,
        "upload_form": upload_form or WorkbookUploadForm(), "generation_form": form,
    }


@login_required
def upload_workbook(request):
    if request.method != "POST":
        return redirect("dashboard")
    form = WorkbookUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        return render(request, "planner/dashboard.html", _dashboard_context(request, form), status=400)
    uploaded = form.cleaned_data["workbook"]
    try:
        parsed = parse_workbook(uploaded)
    except WorkbookImportError as exc:
        form.add_error("workbook", str(exc))
        return render(request, "planner/dashboard.html", _dashboard_context(request, form), status=400)
    try:
        with transaction.atomic():
            batch = WorkbookImport(uploaded_by=request.user, source_filename=Path(uploaded.name).name, status="warning" if parsed["warnings"] else "ready", sheet_summary=parsed["sheet_summary"], warnings=parsed["warnings"])
            batch.source_file.save(Path(uploaded.name).name, uploaded, save=False)
            batch.save()
            save_import_records(batch, parsed)
            import_workbook_history(batch)
    except Exception as exc:
        if "batch" in locals() and batch.pk:
            try:
                batch.source_file.delete(save=False)
                batch.delete()
            except Exception:
                logger.exception("Could not clean up a failed workbook import for batch %s", batch.pk)
        logger.exception("Workbook import failed for user %s and file %s", request.user.pk, Path(uploaded.name).name)
        form.add_error("workbook", f"The workbook could not be saved: {exc}")
        return render(request, "planner/dashboard.html", _dashboard_context(request, form), status=500)
    messages.success(request, f"Imported {batch.source_filename}: {len(parsed['sheet_summary'])} worksheets and {parsed['employee_count']} unique names detected.")
    for warning in parsed["warnings"][:5]:
        messages.warning(request, warning)
    return redirect("workbook_detail", workbook_id=batch.pk)


@login_required
def workbook_detail(request, workbook_id):
    batch = get_object_or_404(WorkbookImport, pk=workbook_id, uploaded_by=request.user)
    employees = list(Employee.objects.filter(
        owner=request.user,
        category_assignments__roster_sheet__workbook=batch,
    ).distinct().order_by("display_name"))
    excluded_employee_ids = set(EmployeeAssignment.objects.filter(
        roster_sheet__workbook=batch,
        excluded_from_roster=True,
    ).values_list("employee_id", flat=True))
    for employee in employees:
        employee.removed_from_roster = employee.pk in excluded_employee_ids
    saved_history = history_status(request.user, date(timezone.localdate().year, timezone.localdate().month, 1))
    base_file_sheet = _ensure_staffing_reference_data(batch)
    return render(request, "planner/workbook_detail.html", {
        "batch": batch,
        "employees": employees,
        "generation_form": GenerationForm(workbook_import=batch),
        "staffing_form": StaffingConfigurationForm(owner=request.user, workbook_import=batch),
        "history_months": saved_history["months"],
        "history_missing_months": saved_history["missing_months"],
        "history_record_count": HistoricalRosterSnapshot.objects.filter(owner=request.user).count(),
        "pattern_change_count": EmployeePatternChange.objects.filter(employee__owner=request.user).count(),
        "history_baseline": PatternHistoryBaseline.objects.filter(owner=request.user).first(),
        "staffing_targets": StaffingRequirement.objects.filter(owner=request.user, effective_to__isnull=True),
        "base_file_sheet": base_file_sheet,
    })


@login_required
def configure_staffing(request, workbook_id):
    batch = get_object_or_404(WorkbookImport, pk=workbook_id, uploaded_by=request.user)
    form = StaffingConfigurationForm(request.POST or None, owner=request.user, workbook_import=batch)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Staffing requirements were saved for reuse in future rosters.")
        return redirect("workbook_detail", workbook_id=batch.pk)
    return render(request, "planner/staffing_config.html", {"batch": batch, "form": form, "base_file_sheet": _ensure_staffing_reference_data(batch)})


def _ensure_staffing_reference_data(batch):
    sheet = batch.sheets.filter(layout_kind="staffing_reference").first()
    if sheet and not sheet.data_rows and batch.source_file:
        with batch.source_file.open("rb") as source:
            parsed = parse_workbook(source)
        source_sheet = next((item for item in parsed["sheets"] if item["name"] == sheet.name), None)
        if source_sheet:
            sheet.data_rows = source_sheet["rows"]
            sheet.row_count = len(sheet.data_rows)
            sheet.source_headers = source_sheet.get("header_fields", [])
            sheet.save(update_fields=["data_rows", "row_count", "source_headers"])
    return sheet


@login_required
def import_roster_history(request, workbook_id):
    if request.method != "POST":
        return redirect("workbook_detail", workbook_id=workbook_id)
    batch = get_object_or_404(WorkbookImport, pk=workbook_id, uploaded_by=request.user)
    count = sum(import_workbook_history(item) for item in WorkbookImport.objects.filter(uploaded_by=request.user))
    messages.success(request, f"Historical roster data from all imported workbooks is saved. {count} new employee-month records were added.")
    return redirect("workbook_detail", workbook_id=batch.pk)


@login_required
def confirm_pattern_baseline(request, workbook_id):
    batch = get_object_or_404(WorkbookImport, pk=workbook_id, uploaded_by=request.user)
    form = PatternBaselineForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        PatternHistoryBaseline.objects.update_or_create(owner=request.user, defaults={"effective_from": form.cleaned_data["effective_from"], "note": form.cleaned_data["note"], "confirmed_by": request.user})
        messages.success(request, "Prospective pattern tracking baseline recorded. Earlier history remains unverified.")
        return redirect("workbook_detail", workbook_id=batch.pk)
    return render(request, "planner/pattern_baseline.html", {"batch": batch, "form": form})


@login_required
def edit_employee(request, workbook_id, employee_id):
    batch = get_object_or_404(WorkbookImport, pk=workbook_id, uploaded_by=request.user)
    employee = get_object_or_404(
        Employee.objects.filter(owner=request.user, category_assignments__roster_sheet__workbook=batch).distinct(),
        pk=employee_id,
    )
    old_name_key = employee.normalized_name
    form = EmployeeDetailsForm(request.POST or None, instance=employee)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            updated_employee = form.save(commit=False)
            updated_employee.owner = request.user
            updated_employee.save()
            if updated_employee.normalized_name != old_name_key:
                sheets = RosterSheet.objects.filter(workbook__uploaded_by=request.user, layout_kind="roster")
                for sheet in sheets:
                    changed = False
                    for row in sheet.data_rows:
                        existing_key = " ".join(str(row.get("name", "")).casefold().split())
                        if existing_key == old_name_key:
                            row["name"] = updated_employee.display_name
                            changed = True
                    if changed:
                        sheet.save(update_fields=["data_rows"])
        messages.success(request, f"Updated {updated_employee.display_name}. Changes will be used in future roster drafts.")
        return redirect("workbook_detail", workbook_id=batch.pk)
    return render(request, "planner/employee_edit.html", {"batch": batch, "employee": employee, "form": form})


@login_required
def toggle_employee_roster(request, workbook_id, employee_id):
    if request.method != "POST":
        return redirect("workbook_detail", workbook_id=workbook_id)
    batch = get_object_or_404(WorkbookImport, pk=workbook_id, uploaded_by=request.user)
    employee = get_object_or_404(
        Employee.objects.filter(owner=request.user, category_assignments__roster_sheet__workbook=batch).distinct(),
        pk=employee_id,
    )
    assignments = EmployeeAssignment.objects.filter(employee=employee, roster_sheet__workbook=batch)
    exclude = request.POST.get("action") == "remove"
    assignments.update(excluded_from_roster=exclude)
    if exclude:
        messages.success(request, f"{employee.display_name} was removed from this workbook's future roster drafts.")
    else:
        messages.success(request, f"{employee.display_name} was restored to this workbook's future roster drafts.")
    return redirect("workbook_detail", workbook_id=batch.pk)


@login_required
def generate_roster(request):
    if request.method != "POST":
        return redirect("dashboard")
    source_id = request.POST.get("source_id")
    batch = get_object_or_404(WorkbookImport, pk=source_id, uploaded_by=request.user)
    form = GenerationForm(request.POST, workbook_import=batch)
    if not form.is_valid():
        for error in form.errors.values():
            for item in error:
                messages.error(request, item)
        return redirect("workbook_detail", workbook_id=batch.pk)
    categories = [form.cleaned_data["location"]]
    target_month = date(form.cleaned_data["target_year"], form.cleaned_data["target_month"], 1)
    saved_requirements = StaffingRequirement.objects.filter(owner=request.user, effective_from__lte=target_month).filter(Q(effective_to__isnull=True) | Q(effective_to__gte=target_month))
    requirements = {}
    for requirement in saved_requirements.order_by("effective_from"):
        if requirement.specific_date and (requirement.specific_date.year, requirement.specific_date.month) != (target_month.year, target_month.month):
            continue
        requirement_day = requirement.specific_date.isoformat() if requirement.specific_date else requirement.day_type
        requirements[(requirement.facility, requirement.shift_code, requirement_day)] = requirement.required_count
    configured_requirements = dict(requirements)
    missing_targets = []
    inferred_requirements = set()
    target_facilities = categories or list(batch.sheets.filter(layout_kind="roster", name__in=["HDC3", "HDC4", "HDC5"]).values_list("name", flat=True))
    source_sheets = {sheet.name: sheet for sheet in batch.sheets.filter(layout_kind="roster", name__in=target_facilities)}
    for facility in target_facilities:
        for day_type, day_value in (("weekday", 0), ("weekend", 5)):
            sample = date(target_month.year, target_month.month, 1)
            while (sample.weekday() >= 5) != (day_value == 5):
                sample += timedelta(days=1)
            for shift in _required_shifts(facility, sample):
                key = (facility, shift, day_type)
                fixed_target = _fixed_coverage_target(facility, sample, shift)
                if fixed_target is not None:
                    # Facility coverage rules are mandatory and take precedence
                    # over any saved configurable target for the same shift.
                    requirements[key] = fixed_target
                    continue
                if key not in configured_requirements:
                    missing_targets.append(f"{facility} {shift} {day_type}")
                    inferred_requirements.add(key)
                    source_sheet = source_sheets.get(facility)
                    inferred = _inferred_coverage_target(
                        source_sheet.data_rows if source_sheet else [],
                        source_sheet.roster_month if source_sheet and source_sheet.roster_month else target_month,
                        sample,
                        shift,
                    )
                    requirements[key] = inferred
    if missing_targets:
        warnings_note = ", ".join(sorted(set(missing_targets)))
    else:
        warnings_note = ""
    history = history_status(request.user, target_month)
    assignments, warnings, used_categories, export_sheets = create_roster_preview(
        batch,
        target_month,
        categories,
        public_holidays=form.cleaned_data["public_holidays"],
        staffing_requirements=requirements,
        history_complete=history["complete_for_target_year"],
    )
    for warning in warnings:
        if warning.get("code") != "staffing_shortage" or not warning.get("date"):
            continue
        work_day = date.fromisoformat(warning["date"])
        day_type = "weekend" if work_day.weekday() >= 5 else "weekday"
        if (warning.get("sheet"), warning.get("shift"), day_type) in inferred_requirements:
            warning["severity"] = "warning"
            warning["code"] = "inferred_staffing_variance"
    if missing_targets:
        warnings.append({"severity": "warning", "code": "staffing_configuration_incomplete", "message": "Provisional staffing targets were inferred from matching weekdays in the source roster for: " + warnings_note + ". These targets are not confirmed requirements."})
    if not assignments:
        messages.error(request, "No roster rows were available to project.")
        return redirect("workbook_detail", workbook_id=batch.pk)
    warnings.append({"severity": "info", "code": "formula_recalculation", "message": "Excel is set to recalculate formulas when the copied workbook is opened."})
    coverage = _staffing_coverage(assignments, used_categories, target_month, requirements)
    for item in coverage:
        if item["shortage"] or item["excess"]:
            day_type = "weekend" if date.fromisoformat(item["date"]).weekday() >= 5 else "weekday"
            if (item["facility"], item["shift"], day_type) in inferred_requirements:
                warnings.append({"severity": "warning", "code": "inferred_staffing_variance", "sheet": item["facility"], "shift": item["shift"], "date": item["date"], "required": item["required"], "actual": item["actual"], "message": f"The source roster suggests {item['required']} staff for {item['facility']} shift {item['shift']}, but this provisional draft assigns {item['actual']}. The target is inferred, not confirmed."})
                continue
            issue_code = "staffing_shortage" if item["shortage"] else "staffing_excess"
            variance = f"short by {item['shortage']}" if item["shortage"] else f"over by {item['excess']}"
            warnings.append({"severity": "error", "code": issue_code, "sheet": item["facility"], "shift": item["shift"], "date": item["date"], "required": item["required"], "actual": item["actual"], "message": f"{item['facility']} shift {item['shift']} is {variance} on {item['date']}; {item['actual']} assigned against a target of {item['required']}."})
    hard_errors = [item for item in warnings if item.get("severity") == "error"]
    unresolved = any(item.get("code") in {"staffing_targets_unconfigured", "annual_history_unavailable", "staffing_configuration_incomplete", "month_not_detected", "missing_day_columns"} for item in warnings)
    validation_status = "INFEASIBLE" if any(item.get("code") in {"coverage_gap", "staffing_shortage"} for item in hard_errors) else "INVALID" if hard_errors else "NEEDS_ATTENTION" if unresolved else "VALID"
    staffing_state = "MISMATCH" if any(item["shortage"] or item["excess"] for item in coverage) else "INFERRED" if missing_targets else "CONFIGURED"
    validation_report = {"rules": "INVALID" if hard_errors else "VALID", "staffing": staffing_state, "history": "COMPLETE" if history["complete_for_target_year"] else "INCOMPLETE", "feasibility": "INFEASIBLE" if validation_status == "INFEASIBLE" else "NOT_FEASIBLE" if validation_status == "INVALID" else "FEASIBLE", "overall": validation_status, "coverage": coverage, "history_months": [item.isoformat() for item in history["months"]], "baseline_effective_from": history["baseline"].effective_from.isoformat() if history["baseline"] else None}
    try:
        if any(item.get("consolidated") for item in batch.sheet_summary):
            output, export_report = export_consolidated_workbook(assignments, target_month, validation_report, warnings)
        else:
            with batch.source_file.open("rb") as source:
                output, export_report = export_workbook(source, export_sheets, assignments, target_month, validation_report, warnings)
        for message in export_report["layout_warnings"][:100]:
            warnings.append({"severity": "warning", "code": "export_layout", "message": message})
        run = GenerationRun.objects.create(source=batch, created_by=request.user, target_month=target_month, categories=used_categories, status="review", validation_status=validation_status, validation_report=validation_report, warnings=warnings, assignments=assignments)
        output_name = f"Roster_{target_month:%B_%Y}_preview.xlsx"
        run.output_file.save(output_name, ContentFile(output.read()), save=True)
    except Exception as exc:
        messages.error(request, f"A preview could not be exported: {exc}")
        return redirect("workbook_detail", workbook_id=batch.pk)
    return redirect("run_detail", run_id=run.pk)


def _staffing_coverage(assignments, categories, target_month, requirements):
    report = []
    for facility in categories:
        for day_number in range(1, _month_days(target_month) + 1):
            current = date(target_month.year, target_month.month, day_number)
            day_type = "weekend" if current.weekday() >= 5 else "weekday"
            for (req_facility, shift, req_day_type), required in requirements.items():
                if req_facility != facility or req_day_type != day_type:
                    continue
                required = requirements.get((facility, shift, current.isoformat()), required)
                if (facility, shift, current.isoformat()) in requirements:
                    continue
                actual = sum(1 for row in assignments if row.get("sheet") == facility and row.get("assignments", [])[day_number - 1] == shift)
                report.append({"facility": facility, "date": current.isoformat(), "shift": shift, "required": required, "actual": actual, "shortage": max(0, required - actual), "excess": max(0, actual - required)})
            for (req_facility, shift, req_date), required in requirements.items():
                if req_facility == facility and req_date == current.isoformat():
                    actual = sum(1 for row in assignments if row.get("sheet") == facility and row.get("assignments", [])[day_number - 1] == shift)
                    report.append({"facility": facility, "date": current.isoformat(), "shift": shift, "required": required, "actual": actual, "shortage": max(0, required - actual), "excess": max(0, actual - required)})
    return report


@login_required
def run_detail(request, run_id):
    run = get_object_or_404(GenerationRun.objects.select_related("source"), pk=run_id, created_by=request.user)
    day_count = calendar.monthrange(run.target_month.year, run.target_month.month)[1]
    target_dates = [date(run.target_month.year, run.target_month.month, day) for day in range(1, day_count + 1)]
    errors = [item for item in run.warnings if item.get("severity") == "error"]
    structural_limitations = any(item.get("code") in {"month_not_detected", "missing_day_columns", "no_roster_sheets", "export_layout"} for item in run.warnings)
    provisional_download = run.validation_status == "NEEDS_ATTENTION" and not errors and not structural_limitations
    assigned_count = sum(1 for row in run.assignments for code in row.get("assignments", []) if code in {"G", "1", "2", "3"})
    return render(request, "planner/run_detail.html", {
        "run": run, "employee_rows": run.assignments, "target_dates": target_dates,
        "assigned_count": assigned_count, "uncovered_count": sum(1 for item in errors if item.get("code") in {"coverage_gap", "general_coverage_gap", "staffing_shortage"}),
        "blocking_errors": errors, "validation_report": run.validation_report, "provisional_download": provisional_download,
    })


@login_required
def approve_run(request, run_id):
    run = get_object_or_404(GenerationRun, pk=run_id, created_by=request.user)
    if request.method != "POST" or request.POST.get("confirm_review") != "yes":
        return redirect("run_detail", run_id=run.pk)
    blocking = [item for item in run.warnings if item.get("severity") == "error"]
    if blocking or run.validation_status != "VALID":
        messages.error(request, f"This roster cannot be certified as VALID (status: {run.validation_status}). Resolve hard conflicts and complete staffing/history setup first.")
    else:
        for row in run.assignments:
            employee = Employee.objects.filter(owner=request.user, pk=row.get("employee_id")).first()
            if not employee:
                continue
            old_pattern = row.get("previous_pattern", "")
            new_pattern = _previous_shift({"assignments": {str(i): code for i, code in enumerate(row.get("assignments", []), start=1)}})
            if new_pattern and old_pattern and new_pattern != old_pattern:
                prior_change = EmployeePatternChange.objects.filter(employee=employee, effective_end__isnull=True, effective_start__lt=run.target_month).order_by("-effective_start").first()
                if prior_change:
                    prior_change.effective_end = run.target_month.fromordinal(run.target_month.toordinal() - 1)
                    prior_change.save(update_fields=["effective_end"])
                EmployeePatternChange.objects.get_or_create(
                    employee=employee,
                    effective_start=run.target_month,
                    new_pattern=new_pattern,
                    defaults={"previous_pattern": old_pattern, "changed_on": run.target_month, "source": "approved_generation", "generation_run": run},
                )
        run.status = "approved"
        run.save(update_fields=["status"])
        messages.success(request, "Draft marked approved. Review and operational sign-off remain your responsibility.")
    return redirect("run_detail", run_id=run.pk)


@login_required
def download_run(request, run_id):
    run = get_object_or_404(GenerationRun, pk=run_id, created_by=request.user)
    if not run.output_file:
        raise Http404("No output workbook is available.")
    blocking_errors = any(item.get("severity") == "error" for item in run.warnings)
    structural_limitations = any(item.get("code") in {"month_not_detected", "missing_day_columns", "no_roster_sheets", "export_layout"} for item in run.warnings)
    certified = run.status == "approved" and run.validation_status == "VALID"
    provisional = run.validation_status == "NEEDS_ATTENTION" and not structural_limitations
    if blocking_errors or not (certified or provisional):
        messages.error(request, "This workbook cannot be downloaded because mandatory conflicts or structural limitations remain unresolved.")
        return redirect("run_detail", run_id=run.pk)
    suffix = "_provisional" if provisional else ""
    return FileResponse(run.output_file.open("rb"), as_attachment=True, filename=f"Roster_{run.target_month:%B_%Y}{suffix}.xlsx")

import calendar
from io import BytesIO
import logging
import re
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

from planner.forms import EmployeeDetailsForm, PatternBaselineForm, SignUpForm, StaffingConfigurationForm, WorkbookUploadForm
from planner.models import Employee, EmployeeAssignment, EmployeePatternChange, GenerationRun, HistoricalRosterSnapshot, LeaveNote, PatternHistoryBaseline, PublicHoliday, RosterSheet, StaffingRequirement, WorkbookImport
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
    latest = WorkbookImport.objects.filter(uploaded_by=request.user).order_by("-uploaded_at").first()
    if latest and not latest.generation_runs.exists():
        target_groups, issue = _next_target_month_groups(latest)
        if issue:
            messages.error(request, issue)
            return redirect("workbook_detail", workbook_id=latest.pk)
        runs, failed_months = _generate_roster_groups(request, latest, target_groups)
        if not runs:
            messages.error(request, "Automatic generation failed. The uploaded source roster is available to inspect.")
            return redirect("workbook_detail", workbook_id=latest.pk)
        message = f"Automatically generated {len(runs)} next-month roster(s) from the source month groups."
        if failed_months:
            message += " Some month groups failed: " + ", ".join(failed_months) + "."
        messages.success(request, message)
        return redirect("run_detail", run_id=runs[-1].pk)
    return render(request, "planner/dashboard.html", _dashboard_context(request))


def _dashboard_context(request, upload_form=None):
    imports = WorkbookImport.objects.filter(uploaded_by=request.user).order_by("-uploaded_at")
    latest = imports.first()
    runs = GenerationRun.objects.filter(created_by=request.user).select_related("source")[:6]
    return {
        "imports": imports[:6], "latest": latest, "runs": runs,
        "latest_run": latest.generation_runs.first() if latest else None,
        "upload_form": upload_form or WorkbookUploadForm(),
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
    target_groups, issue = _next_target_month_groups(batch)
    if issue:
        messages.error(request, issue)
        return redirect("workbook_detail", workbook_id=batch.pk)
    runs, failed_months = _generate_roster_groups(request, batch, target_groups)
    if not runs:
        messages.error(request, "Automatic generation failed. The uploaded source roster is available to inspect.")
        return redirect("workbook_detail", workbook_id=batch.pk)
    message = f"Automatically generated {len(runs)} next-month roster(s) from the source month groups."
    if failed_months:
        message += " Some month groups failed: " + ", ".join(failed_months) + "."
    messages.success(request, message)
    return redirect("run_detail", run_id=runs[-1].pk)


def _next_target_month_groups(batch):
    sheets = list(batch.sheets.filter(layout_kind="roster"))
    if not sheets:
        return {}, "No employee roster tabs or location rows were detected in the uploaded workbook."
    if any(not sheet.roster_month for sheet in sheets):
        missing = ", ".join(sheet.name for sheet in sheets if not sheet.roster_month)
        return {}, f"The source month could not be detected for {missing}; automatic month selection is unsafe."
    groups = {}
    for sheet in sheets:
        source_month = date(sheet.roster_month.year, sheet.roster_month.month, 1)
        target_month = date(source_month.year + (source_month.month == 12), 1 if source_month.month == 12 else source_month.month + 1, 1)
        groups.setdefault(target_month, source_month)
    return groups, None


def _next_target_month(batch):
    """Compatibility helper for a single-month upload."""
    groups, issue = _next_target_month_groups(batch)
    if issue:
        return None, issue
    if len(groups) != 1:
        return None, "This workbook contains multiple source months; generate one next-month roster for each month group."
    return next(iter(groups)), None


def _generate_roster_groups(request, batch, target_groups):
    runs, failed_months = [], []
    with batch.source_file.open("rb") as source_file:
        source_bytes = source_file.read()
    for target_month, source_month in sorted(target_groups.items()):
        holidays = set(PublicHoliday.objects.filter(
            confirmed=True, day__year=target_month.year, day__month=target_month.month,
        ).values_list("day", flat=True))
        try:
            run = _generate_roster_for_batch(
                request, batch, target_month, public_holidays=holidays,
                source_month=source_month,
                source_bytes=source_bytes,
            )
        except Exception:
            logger.exception("Automatic roster generation failed for import %s and target %s", batch.pk, target_month)
            failed_months.append(target_month.strftime("%B %Y"))
            continue
        if run:
            runs.append(run)
    return runs, failed_months


def _source_visualization_sheets(batch, source_month=None):
    visual_sheets = []
    source_sheets = batch.sheets.filter(layout_kind="roster")
    if source_month:
        source_sheets = source_sheets.filter(roster_month=source_month)
    for sheet in source_sheets:
        source_rows = sheet.data_rows
        day_count = max((len(row.get("assignments", {})) for row in source_rows), default=0)
        dates = []
        if sheet.roster_month:
            for day_number in range(1, day_count + 1):
                try:
                    current = date(sheet.roster_month.year, sheet.roster_month.month, day_number)
                except ValueError:
                    continue
                dates.append(current)
        visual_rows = [{
            "name": row.get("name", ""), "category": row.get("category", ""),
            "assignments": [row.get("assignments", {}).get(str(day), "") for day in range(1, day_count + 1)],
        } for row in source_rows]
        visual_sheets.append({"name": sheet.name, "month": sheet.roster_month, "dates": dates, "rows": visual_rows})
    return visual_sheets


def _leave_inputs_from_workbook(batch, target_month):
    leave_by_employee = {}
    unresolved_count = 0
    month_start = target_month
    month_end = date(target_month.year, target_month.month, calendar.monthrange(target_month.year, target_month.month)[1])
    date_pattern = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?:\s*(?:to|through|[-–])\s*(\d{4}-\d{2}-\d{2}))?(?!\d)", re.IGNORECASE)
    for note in LeaveNote.objects.filter(workbook=batch):
        raw = " ".join(filter(None, [note.raw_date_text, note.raw_note])).strip()
        matches = list(date_pattern.finditer(raw))
        if not matches:
            if raw:
                unresolved_count += 1
            continue
        normalized_name = " ".join(note.employee_name.casefold().split())
        for match in matches:
            try:
                start = date.fromisoformat(match.group(1))
                end = date.fromisoformat(match.group(2) or match.group(1))
            except ValueError:
                unresolved_count += 1
                continue
            if end < start:
                unresolved_count += 1
                continue
            if end < month_start or start > month_end:
                continue
            leave_by_employee.setdefault(normalized_name, []).append({
                "start": max(start, month_start).isoformat(),
                "end": min(end, month_end).isoformat(),
            })
    return leave_by_employee, unresolved_count


def _has_saved_leave_in_month(owner, batch, target_month):
    month_end = date(target_month.year, target_month.month, calendar.monthrange(target_month.year, target_month.month)[1])
    employees = Employee.objects.filter(owner=owner, category_assignments__roster_sheet__workbook=batch).distinct()
    for employee in employees:
        for item in employee.planned_leave:
            try:
                start = date.fromisoformat(item["start"])
                end = date.fromisoformat(item["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if start <= month_end and end >= target_month:
                return True
    return False


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
    latest_run = batch.generation_runs.first()
    generated_runs = list(batch.generation_runs.all())
    for generated_run in generated_runs:
        has_errors = any(item.get("severity") == "error" for item in generated_run.warnings)
        has_structural_limitations = any(item.get("code") in {"month_not_detected", "missing_day_columns", "no_roster_sheets", "export_layout"} for item in generated_run.warnings)
        generated_run.downloadable = generated_run.validation_status in {"VALID", "NEEDS_ATTENTION"} and not has_errors and not has_structural_limitations
    latest_downloadable = False
    if latest_run:
        latest_errors = any(item.get("severity") == "error" for item in latest_run.warnings)
        latest_structural = any(item.get("code") in {"month_not_detected", "missing_day_columns", "no_roster_sheets", "export_layout"} for item in latest_run.warnings)
        latest_downloadable = latest_run.validation_status in {"VALID", "NEEDS_ATTENTION"} and not latest_errors and not latest_structural
    return render(request, "planner/workbook_detail.html", {
        "batch": batch,
        "employees": employees,
        "source_roster_sheets": _source_visualization_sheets(batch),
        "latest_run": latest_run,
        "generated_runs": generated_runs,
        "latest_downloadable": latest_downloadable,
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
    target_month, issue = _next_target_month(batch)
    if issue:
        messages.error(request, issue)
        return redirect("workbook_detail", workbook_id=batch.pk)
    holidays = set(PublicHoliday.objects.filter(
        confirmed=True, day__year=target_month.year, day__month=target_month.month,
    ).values_list("day", flat=True))
    run = _generate_roster_for_batch(request, batch, target_month, public_holidays=holidays)
    if run:
        return redirect("run_detail", run_id=run.pk)
    return redirect("workbook_detail", workbook_id=batch.pk)


def _generate_roster_for_batch(request, batch, target_month, public_holidays=None, source_month=None, source_bytes=None):
    source_queryset = batch.sheets.filter(layout_kind="roster")
    if source_month:
        source_queryset = source_queryset.filter(roster_month=source_month)
    source_sheets_for_month = list(source_queryset)
    categories = list(dict.fromkeys(sheet.name for sheet in source_sheets_for_month))
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
    target_facilities = categories
    source_sheets = {}
    for source_sheet in source_sheets_for_month:
        if source_sheet.name in target_facilities:
            source_sheets.setdefault(source_sheet.name, []).extend(source_sheet.data_rows)
    unsupported_locations = []
    for facility in target_facilities:
        if facility not in {"HDC3", "HDC4", "HDC5"}:
            unsupported_locations.append(facility)
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
                        source_sheet if source_sheet else [],
                        source_month or target_month,
                        sample,
                        shift,
                    )
                    requirements[key] = inferred
        # Keep the coverage report aligned with mandatory daily counts too,
        # including any previously saved date-specific override.
        for day_number in range(1, _month_days(target_month) + 1):
            current_day = date(target_month.year, target_month.month, day_number)
            for shift in _required_shifts(facility, current_day):
                fixed_target = _fixed_coverage_target(facility, current_day, shift)
                if fixed_target is not None:
                    requirements[(facility, shift, current_day.isoformat())] = fixed_target
    if missing_targets:
        warnings_note = ", ".join(sorted(set(missing_targets)))
    else:
        warnings_note = ""
    history = history_status(request.user, target_month)
    leave_by_employee, unresolved_leave_notes = _leave_inputs_from_workbook(batch, target_month)
    assignments, warnings, used_categories, export_sheets = create_roster_preview(
        batch,
        target_month,
        categories,
        public_holidays=public_holidays or set(),
        staffing_requirements=requirements,
        history_complete=history["complete_for_target_year"],
        leave_by_employee=leave_by_employee,
        source_month=source_month,
    )
    inferred_source_months = [
        item for item in batch.sheet_summary
        if item.get("month_inferred") and item.get("month") == source_month.isoformat()
    ] if source_month else []
    for item in inferred_source_months:
        warnings.append({"severity": "warning", "code": "source_month_inferred", "sheet": item["name"], "message": f"The {item['name']!r} month label disagreed with its day and weekday headers. The source period was inferred as {source_month:%B %Y}; verify the source tab before treating this roster as final."})
    if any(item.get("layout_kind") == "staffing_reference" for item in batch.sheet_summary):
        warnings.append({"severity": "warning", "code": "staffing_reference_unmapped", "message": "A staffing count table was found in the workbook, but its date and shift mapping is ambiguous. It was retained as reference data; provisional targets are inferred from actual assignments and are not confirmed."})
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
    if unsupported_locations:
        warnings.append({"severity": "warning", "code": "location_rules_unavailable", "message": "No location-specific coverage rules were provided for: " + ", ".join(sorted(set(unsupported_locations))) + ". Shift and coverage assignments for these locations are provisional."})
    if not public_holidays:
        warnings.append({"severity": "warning", "code": "public_holiday_calendar_unavailable", "message": f"No confirmed public-holiday dates were available for {target_month:%B %Y}; holiday-adjacent leave blocks could not be checked."})
    if not leave_by_employee and not _has_saved_leave_in_month(request.user, batch, target_month):
        warnings.append({"severity": "warning", "code": "leave_data_unavailable", "message": "No future leave dates were found in the uploaded leave details or saved employee records; leave-email dates cannot be inferred from last month's assignments."})
    if unresolved_leave_notes:
        warnings.append({"severity": "warning", "code": "leave_dates_unresolved", "message": f"{unresolved_leave_notes} leave note(s) did not contain an unambiguous ISO date or date range and were not applied."})
    if not assignments:
        messages.error(request, "No roster rows were available to project.")
        return None
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
    unresolved = any(item.get("code") in {"staffing_targets_unconfigured", "annual_history_unavailable", "staffing_configuration_incomplete", "month_not_detected", "missing_day_columns", "location_rules_unavailable", "public_holiday_calendar_unavailable", "leave_data_unavailable", "leave_dates_unresolved", "source_month_inferred", "staffing_reference_unmapped", "source_marker_review"} for item in warnings)
    validation_status = "INFEASIBLE" if any(item.get("code") in {"coverage_gap", "staffing_shortage"} for item in hard_errors) else "INVALID" if hard_errors else "NEEDS_ATTENTION" if unresolved else "VALID"
    staffing_state = "MISMATCH" if any(item["shortage"] or item["excess"] for item in coverage) else "INFERRED" if missing_targets else "CONFIGURED"
    validation_report = {"rules": "INVALID" if hard_errors else "VALID", "staffing": staffing_state, "history": "COMPLETE" if history["complete_for_target_year"] else "INCOMPLETE", "feasibility": "INFEASIBLE" if validation_status == "INFEASIBLE" else "NOT_FEASIBLE" if validation_status == "INVALID" else "FEASIBLE", "overall": validation_status, "coverage": coverage, "history_months": [item.isoformat() for item in history["months"]], "baseline_effective_from": history["baseline"].effective_from.isoformat() if history["baseline"] else None}
    try:
        needs_expanded_layout = any(item.get("day_count", 0) < _month_days(target_month) for item in export_sheets)
        if any(item.get("consolidated") for item in batch.sheet_summary) or needs_expanded_layout:
            output, export_report = export_consolidated_workbook(assignments, target_month, validation_report, warnings)
        else:
            source = BytesIO(source_bytes) if source_bytes is not None else batch.source_file.open("rb")
            try:
                output, export_report = export_workbook(source, export_sheets, assignments, target_month, validation_report, warnings)
            finally:
                source.close()
        for message in export_report["layout_warnings"][:100]:
            warnings.append({"severity": "warning", "code": "export_layout", "message": message})
        run = GenerationRun.objects.create(source=batch, created_by=request.user, target_month=target_month, categories=used_categories, status="review", validation_status=validation_status, validation_report=validation_report, warnings=warnings, assignments=assignments)
        output_name = f"Roster_{target_month:%B_%Y}_preview.xlsx"
        run.output_file.save(output_name, ContentFile(output.read()), save=True)
    except Exception as exc:
        messages.error(request, f"A preview could not be exported: {exc}")
        return None
    return run


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
    downloadable = run.validation_status in {"VALID", "NEEDS_ATTENTION"} and not errors and not structural_limitations
    other_runs = list(run.source.generation_runs.exclude(pk=run.pk).order_by("target_month"))
    for other_run in other_runs:
        other_errors = any(item.get("severity") == "error" for item in other_run.warnings)
        other_structural = any(item.get("code") in {"month_not_detected", "missing_day_columns", "no_roster_sheets", "export_layout"} for item in other_run.warnings)
        other_run.downloadable = other_run.validation_status in {"VALID", "NEEDS_ATTENTION"} and not other_errors and not other_structural
    assigned_count = sum(1 for row in run.assignments for code in row.get("assignments", []) if code in {"G", "1", "2", "3"})
    return render(request, "planner/run_detail.html", {
        "run": run, "employee_rows": run.assignments, "target_dates": target_dates,
        "source_roster_sheets": _source_visualization_sheets(run.source, date(run.target_month.year - (run.target_month.month == 1), 12 if run.target_month.month == 1 else run.target_month.month - 1, 1)),
        "other_runs": other_runs,
        "assigned_count": assigned_count, "uncovered_count": sum(1 for item in errors if item.get("code") in {"coverage_gap", "general_coverage_gap", "staffing_shortage"}),
        "blocking_errors": errors, "validation_report": run.validation_report,
        "downloadable": downloadable, "provisional_download": downloadable and run.validation_status == "NEEDS_ATTENTION",
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
    downloadable = run.validation_status in {"VALID", "NEEDS_ATTENTION"} and not blocking_errors and not structural_limitations
    if not downloadable:
        messages.error(request, "This workbook cannot be downloaded because mandatory conflicts or structural limitations remain unresolved.")
        return redirect("run_detail", run_id=run.pk)
    suffix = "_provisional" if run.validation_status == "NEEDS_ATTENTION" else ""
    return FileResponse(run.output_file.open("rb"), as_attachment=True, filename=f"Roster_{run.target_month:%B_%Y}{suffix}.xlsx")

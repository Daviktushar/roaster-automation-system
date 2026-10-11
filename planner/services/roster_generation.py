"""A transparent weekday-pattern projection and validation pass.

This creates a review draft, never an auto-approved roster. Date-specific leave
and holiday markers are not carried to another month as if they were confirmed.
"""
from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import median

from planner.models import Employee, EmployeeAssignment, GenerationRun, HistoricalRosterSnapshot
from planner.services.history import would_exceed_annual_pattern_limit


SHIFT_CODES = {"G", "1", "2", "3"}
OFF_CODES = {"WO", "PH"}
LEAVE_ONLY_CODES = {"PL", "UL", "LOA"}


def _previous_shift(row):
    values = [str(value).strip().upper().split("/", 1)[0].strip() for value in row.get("assignments", {}).values()]
    numeric = [(index, value) for index, value in enumerate(values) if value in {"1", "2", "3"}]
    if not numeric:
        return None
    counts = Counter(value for _, value in numeric)
    return max(counts, key=lambda code: (counts[code], max(index for index, value in numeric if value == code)))


def _previous_shifts(row):
    return {str(value).strip().upper().split("/", 1)[0].strip() for value in row.get("assignments", {}).values()} & {"1", "2", "3"}


def _rotated_shift(sheet_name, previous_shift, previous_shifts=None):
    if not previous_shift:
        return None
    previous_shifts = set(previous_shifts or {previous_shift})
    order = {"1": ["2", "3", "1"], "2": ["3", "1", "2"], "3": ["1", "2", "3"]}[previous_shift]
    if sheet_name == "HDC4":
        order = [code for code in order if code in {"1", "2"}]
    return next((code for code in order if code not in previous_shifts), None)


def _required_shifts(sheet_name, current_day):
    weekend = current_day.weekday() >= 5
    if sheet_name == "HDC3":
        return ["1", "2", "3"] if weekend else ["G", "2", "3"]
    if sheet_name == "HDC4":
        return ["1", "2"] if weekend else ["G", "1", "2"]
    if sheet_name == "HDC5":
        return [] if weekend else ["G"]
    return []


def _exact_one_shift(sheet_name, current_day, shift):
    weekend = current_day.weekday() >= 5
    if sheet_name == "HDC3":
        return shift == "3" or (weekend and shift in {"1", "2"})
    if sheet_name == "HDC4":
        return shift == "1"
    return False


def _fixed_coverage_target(sheet_name, current_day, shift):
    """Return the headcount explicitly fixed by the shared facility rules."""
    return 1 if _exact_one_shift(sheet_name, current_day, shift) else None


def _inferred_coverage_target(rows, source_month, target_day, shift):
    """Reuse the median same-weekday headcount when a rule gives no number."""
    source_days = _month_days(source_month)
    observed = []
    for source_day in range(1, source_days + 1):
        if date(source_month.year, source_month.month, source_day).weekday() != target_day.weekday():
            continue
        count = 0
        for row in rows:
            source_assignments = row.get("source_assignments", row.get("assignments", {}))
            value = str(source_assignments.get(str(source_day), "")).strip().upper()
            if value.split("/", 1)[0].strip() == shift:
                count += 1
        observed.append(count)
    return max(1, int(median(observed) + 0.5)) if observed else 1


def _leave_dates(profile, target_month, public_holidays):
    blocked = set()
    if not profile:
        return blocked
    month_days = _month_days(target_month)
    month_dates = {date(target_month.year, target_month.month, day) for day in range(1, month_days + 1)}
    for item in profile.planned_leave:
        try:
            start = date.fromisoformat(item["start"])
            end = date.fromisoformat(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        blocked.update(day for day in month_dates if start <= day <= end)
        for holiday in public_holidays:
            if holiday == start - timedelta(days=1) or holiday == end + timedelta(days=1):
                blocked.add(holiday)
        for day in month_dates:
            if day.weekday() < 5:
                continue
            saturday = day - timedelta(days=day.weekday() - 5)
            sunday = saturday + timedelta(days=1)
            friday, monday = saturday - timedelta(days=1), sunday + timedelta(days=1)
            if end == friday or start == monday or (start <= sunday and end >= saturday):
                blocked.update({saturday, sunday} & month_dates)
    return blocked


def _is_asset_team(sheet_name, category):
    return "asset" in f"{sheet_name} {category or ''}".casefold()


def _facility_allowed(sheet_name, category, current_day):
    weekend = current_day.weekday() >= 5
    if _is_asset_team(sheet_name, category) and weekend:
        return {"WO"}
    if sheet_name == "HDC3":
        return {"1", "2", "3"} if weekend else {"G", "2", "3", "WO", "PH"}
    if sheet_name == "HDC4":
        return {"1", "2"} if weekend else {"G", "1", "2", "WO", "PH"}
    if sheet_name == "HDC5":
        return {"WO"} if weekend else {"G", "WO", "PH"}
    return {"G", "1", "2", "3", "WO", "PH"}


def _build_sheet_assignments(sheet_name, rows, source_month, target_month, public_holidays, staffing_requirements=None):
    """Apply shift rotation, facility coverage, leave, and rest rules for a sheet."""
    target_days = _month_days(target_month)
    staffing_requirements = staffing_requirements or {}
    states = []
    warnings = []
    for row in rows:
        name = row.get("employee", row.get("name", ""))
        profile = row.get("profile")
        category = row.get("activity", row.get("category", ""))
        previous = row.get("previous_shift")
        previous_shifts = set(row.get("previous_shifts", []))
        rotated = row.get("rotation_target")
        # Planned leave is entered on the employee profile. Keep the employee
        # off on the leave dates and on an intervening holiday or weekend.
        leave_blocked = _leave_dates(profile, target_month, public_holidays)
        desired, can_cover, blocked_codes = [], [], []
        for day_number in range(1, target_days + 1):
            current_day = date(target_month.year, target_month.month, day_number)
            code, note = _pattern_code(row, source_month, current_day)
            if row.get("excluded"):
                code = ""
                note = "Employee is inactive or removed from this roster."
            allowed = _facility_allowed(sheet_name, category, current_day)
            # Prior month assignments guide rotation, but only prior night duty
            # is a hard monthly exclusion. Day-shift repetition is a preference.
            if code == "3" and "3" in previous_shifts:
                code = "G" if "G" in _facility_allowed(sheet_name, category, current_day) else "WO"
            if code in {"1", "2", "3", "G"} and code not in allowed:
                code = "G" if "G" in allowed else "WO"
            hard_block = row.get("excluded") or current_day in leave_blocked or (_is_asset_team(sheet_name, category) and current_day.weekday() >= 5)
            can_cover.append(not hard_block and code not in {""})
            blocked_codes.append(note if hard_block else "")
            desired.append(code)
            if note and not hard_block:
                warnings.append({"severity": "warning", "code": "source_marker_review", "sheet": sheet_name, "employee": name, "date": current_day.isoformat(), "message": note})

        source_last_day = _month_days(source_month)
        source_assignments = row.get("source_assignments", {})
        source_last_code = str(source_assignments.get(str(source_last_day), "")).strip().upper().split("/", 1)[0].strip()
        source_streak = 0
        for source_day in range(source_last_day, 0, -1):
            source_code = str(source_assignments.get(str(source_day), "")).strip().upper().split("/", 1)[0].strip()
            if source_code not in SHIFT_CODES:
                break
            source_streak += 1
        states.append({
            "row": row,
            "name": name,
            "category": category,
            "transport_preference": row.get("transport_preference", ""),
            "previous_shift": previous,
            "previous_shifts": previous_shifts,
            "rotation_target": rotated,
            "codes": desired,
            "can_cover": can_cover,
            "blocked_notes": blocked_codes,
            "source_last_code": source_last_code,
            "source_streak": source_streak,
            "leave_blocked_dates": leave_blocked,
            "work_days": 0,
            "shift_days": Counter(),
            "streak": source_streak,
        })

    coverage_gaps = []
    for day_index in range(target_days):
        current_day = date(target_month.year, target_month.month, day_index + 1)
        required = _required_shifts(sheet_name, current_day)
        selected_today = set()
        for state in states:
            code = state["codes"][day_index]
            prev = state["codes"][day_index - 1] if day_index else state["source_last_code"]
            asset_weekend = _is_asset_team(sheet_name, state["category"]) and current_day.weekday() >= 5
            if asset_weekend or not state["can_cover"][day_index]:
                code = "WO" if not state["blocked_notes"][day_index] else ""
            elif state["streak"] >= 5:
                code = "WO"
            elif prev == "3" and code in {"G", "1", "2"}:
                code = "WO"
            state["codes"][day_index] = code

        # Enforce one where the rule specifies a count; other listed shifts need
        # coverage but may retain multiple source-pattern assignments.
        for shift in (code for code in required if code != "G"):
            already = [state for state in states if state["codes"][day_index] == shift]
            valid_already = [state for state in already if shift != "3" or shift not in state["previous_shifts"]]
            day_type = "weekend" if current_day.weekday() >= 5 else "weekday"
            target_count = _fixed_coverage_target(sheet_name, current_day, shift)
            if target_count is None:
                target_count = staffing_requirements.get((sheet_name, shift, current_day.isoformat()), staffing_requirements.get((sheet_name, shift, day_type), _inferred_coverage_target(rows, source_month, current_day, shift)))
            selected_states = sorted(valid_already, key=lambda s: (
                0 if str(s["transport_preference"]).strip().casefold() in {"own", "own vehicle"} else 1,
                s["shift_days"][shift], s["work_days"], s["streak"], s["name"].casefold(),
            ))[:target_count]
            while len(selected_states) < target_count:
                candidates = []
                for state in states:
                    if state in selected_states or not state["can_cover"][day_index] or (shift == "3" and shift in state["previous_shifts"]):
                        continue
                    if id(state) in selected_today:
                        continue
                    if state["codes"][day_index] not in {"G", "1", "2", "3", "WO", "PH"}:
                        continue
                    prev = state["codes"][day_index - 1] if day_index else state["source_last_code"]
                    if state["streak"] >= 5 or (prev == "3" and shift in {"G", "1", "2"}):
                        continue
                    if _is_asset_team(sheet_name, state["category"]) and current_day.weekday() >= 5:
                        continue
                    candidates.append(state)
                if not candidates:
                    break
                selected_states.append(min(candidates, key=lambda s: (
                    0 if str(s["transport_preference"]).strip().casefold() in {"own", "own vehicle"} else 1,
                    s["shift_days"][shift], 0 if s["rotation_target"] == shift else 1,
                    0 if s["codes"][day_index] != "WO" else 1, s["work_days"], s["streak"], s["name"].casefold(),
                )))
            if len(selected_states) < target_count:
                coverage_gaps.append({"severity": "error", "code": "staffing_shortage", "sheet": sheet_name, "shift": shift, "date": current_day.isoformat(), "required": target_count, "actual": len(selected_states), "message": f"{sheet_name} shift {shift} needs {target_count} staff on {current_day:%d %b}; only {len(selected_states)} eligible staff could be assigned."})
            for selected_state in selected_states:
                selected_today.add(id(selected_state))
                selected_state["codes"][day_index] = shift
                selected_state["shift_days"][shift] += 1
            for other in already:
                if other in selected_states:
                    continue
                prior_code = other["codes"][day_index - 1] if day_index else other["source_last_code"]
                can_use_general = (
                    other["can_cover"][day_index]
                    and other["streak"] < 5
                    and prior_code != "3"
                    and "G" in _facility_allowed(sheet_name, other["category"], current_day)
                )
                other["codes"][day_index] = "G" if can_use_general else "WO"

        for state in states:
            code = state["codes"][day_index]
            prior_code = state["codes"][day_index - 1] if day_index else state["source_last_code"]
            if code in {"1", "2", "3"} and code not in required:
                if prior_code != "3" and "G" in _facility_allowed(sheet_name, state["category"], current_day):
                    state["codes"][day_index] = code = "G"
                else:
                    state["codes"][day_index] = code = "WO"
            if code == "G" and "G" not in _facility_allowed(sheet_name, state["category"], current_day):
                state["codes"][day_index] = code = "WO"
            if code == "3" and code in state["previous_shifts"]:
                coverage_gaps.append({"severity": "error", "code": "shift_not_rotated", "sheet": sheet_name, "employee": state["name"], "date": current_day.isoformat(), "message": f"{state['name']} would repeat shift {code} from the source month; the assignment was removed."})
                state["codes"][day_index] = code = "WO"
            if code in {"G", "1", "2", "3"}:
                state["work_days"] += 1
                state["streak"] += 1
            else:
                state["streak"] = 0

        if "G" in required and not any(state["codes"][day_index] == "G" for state in states):
            candidates = [state for state in states if state["can_cover"][day_index] and state["codes"][day_index] in {"WO", "PH"} and state["streak"] < 5 and (state["codes"][day_index - 1] if day_index else state["source_last_code"]) != "3"]
            if candidates:
                selected_general = min(candidates, key=lambda s: (s["work_days"], s["streak"], s["name"].casefold()))
                selected_general["codes"][day_index] = "G"
                selected_general["work_days"] += 1
                selected_general["streak"] += 1
            elif sheet_name in {"HDC3", "HDC4", "HDC5"}:
                coverage_gaps.append({"severity": "error", "code": "general_coverage_gap", "sheet": sheet_name, "date": current_day.isoformat(), "message": f"Could not cover General (G) on {current_day:%d %b}."})
        general_day_type = "weekend" if current_day.weekday() >= 5 else "weekday"
        general_target = _fixed_coverage_target(sheet_name, current_day, "G")
        if general_target is None:
            general_target = staffing_requirements.get((sheet_name, "G", current_day.isoformat()), staffing_requirements.get((sheet_name, "G", general_day_type), _inferred_coverage_target(rows, source_month, current_day, "G") if "G" in required else 0))
        general_assigned = [state for state in states if state["codes"][day_index] == "G"]
        if general_target and len(general_assigned) > general_target:
            ordered = sorted(general_assigned, key=lambda s: (0 if str(s["transport_preference"]).strip().casefold() in {"own", "own vehicle"} else 1, s["work_days"], s["name"].casefold()))
            for extra in ordered[general_target:]:
                extra["codes"][day_index] = "WO"
                extra["work_days"] = max(0, extra["work_days"] - 1)
                extra["streak"] = 0
            general_assigned = ordered[:general_target]
        for _ in range(max(0, general_target - len(general_assigned))):
            candidates = [state for state in states if state not in general_assigned and state["can_cover"][day_index] and state["codes"][day_index] in {"WO", "PH"} and state["streak"] < 5 and (state["codes"][day_index - 1] if day_index else state["source_last_code"]) != "3"]
            if not candidates:
                break
            selected_general = min(candidates, key=lambda s: (0 if str(s["transport_preference"]).strip().casefold() in {"own", "own vehicle"} else 1, s["work_days"], s["streak"], s["name"].casefold()))
            selected_general["codes"][day_index] = "G"
            selected_general["work_days"] += 1
            selected_general["streak"] += 1
            general_assigned.append(selected_general)
        if len(general_assigned) < general_target:
            coverage_gaps.append({"severity": "error", "code": "staffing_shortage", "sheet": sheet_name, "shift": "G", "date": current_day.isoformat(), "required": general_target, "actual": len(general_assigned), "message": f"{sheet_name} General (G) needs {general_target} staff on {current_day:%d %b}; only {len(general_assigned)} eligible staff could be assigned."})

    # A final chronological pass is authoritative for the five-day limit. It
    # also catches assignments moved to General during coverage balancing.
    coverage_gaps = [gap for gap in coverage_gaps if gap.get("code") not in {"staffing_shortage", "general_coverage_gap"}]
    for state in states:
        streak = state["source_streak"]
        previous = state["source_last_code"]
        for day_index, code in enumerate(state["codes"]):
            if code in SHIFT_CODES and (streak >= 5 or (previous == "3" and code in {"G", "1", "2"})):
                current_day = date(target_month.year, target_month.month, day_index + 1)
                state["codes"][day_index] = "WO"
                coverage_gaps.append({"severity": "warning", "code": "rest_day_inserted", "sheet": sheet_name, "employee": state["name"], "date": current_day.isoformat(), "message": f"A weekly off was inserted for {state['name']} to enforce the five-day or shift-3 rest rule."})
                code = "WO"
            if code in SHIFT_CODES:
                streak += 1
            else:
                streak = 0
            previous = code

    # Refill any shift slots opened by rest enforcement when an eligible
    # replacement is available. This preserves required daily coverage.
    for day_index in range(target_days):
        current_day = date(target_month.year, target_month.month, day_index + 1)
        day_type = "weekend" if current_day.weekday() >= 5 else "weekday"
        for shift in _required_shifts(sheet_name, current_day):
            target_count = _fixed_coverage_target(sheet_name, current_day, shift)
            if target_count is None:
                target_count = staffing_requirements.get(
                    (sheet_name, shift, current_day.isoformat()),
                    staffing_requirements.get(
                        (sheet_name, shift, day_type),
                        _inferred_coverage_target(rows, source_month, current_day, shift),
                    ),
                )
            assigned = sum(state["codes"][day_index] == shift for state in states)
            while assigned < target_count:
                candidates = []
                for state in states:
                    if state["codes"][day_index] not in {"WO", "PH", ""} or not state["can_cover"][day_index]:
                        continue
                    if shift not in _facility_allowed(sheet_name, state["category"], current_day):
                        continue
                    if shift == "3" and "3" in state["previous_shifts"]:
                        continue
                    previous = state["codes"][day_index - 1] if day_index else state["source_last_code"]
                    if previous == "3" and shift in {"G", "1", "2"}:
                        continue
                    streak = state["source_streak"]
                    for previous_code in state["codes"][:day_index]:
                        streak = streak + 1 if previous_code in SHIFT_CODES else 0
                    if streak >= 5:
                        continue
                    candidates.append((sum(code in SHIFT_CODES for code in state["codes"][:day_index]), streak, state["name"].casefold(), state))
                if not candidates:
                    break
                chosen = min(candidates, key=lambda item: item[:3])[3]
                chosen["codes"][day_index] = shift
                assigned += 1
                if shift == "3" and day_index + 1 < target_days and chosen["codes"][day_index + 1] in {"G", "1", "2"}:
                    chosen["codes"][day_index + 1] = "WO"
            if assigned < target_count:
                coverage_gaps.append({"severity": "error", "code": "staffing_shortage", "sheet": sheet_name, "shift": shift, "date": current_day.isoformat(), "required": target_count, "actual": assigned, "message": f"{sheet_name} shift {shift} needs {target_count} staff on {current_day:%d %b}; only {assigned} eligible staff could be assigned."})

    output = []
    for state in states:
        row = state["row"]
        output_row = {key: value for key, value in row.items() if key not in {"profile", "source_assignments", "previous_shift", "previous_shifts", "rotation_target", "excluded"}}
        output_row["assignments"] = state["codes"]
        output_row["prior_month_last_code"] = state["source_last_code"]
        output_row["prior_month_work_streak"] = state["source_streak"]
        output_row["prior_month_night_worked"] = "3" in state["previous_shifts"]
        output_row["blocked_dates"] = [day.isoformat() for day in sorted(state["leave_blocked_dates"])]
        output.append(output_row)
    warnings.extend(coverage_gaps)
    return output, warnings


def _month_days(month_start):
    return calendar.monthrange(month_start.year, month_start.month)[1]


def _occurrence(day):
    return (day.day - 1) // 7


def _pattern_code(row, source_month, target_day):
    assignments = row.get("assignments", {})
    source_days = _month_days(source_month)
    same_weekday = [day for day in range(1, source_days + 1) if date(source_month.year, source_month.month, day).weekday() == target_day.weekday()]
    if not same_weekday:
        return "", "No matching weekday in source month."
    occurrence = min(_occurrence(target_day), len(same_weekday) - 1)
    source_day = same_weekday[occurrence]
    raw = str(assignments.get(str(source_day), "")).strip().upper()
    if raw == "PH":
        alternatives = [day for day in same_weekday if str(assignments.get(str(day), "")).strip().upper() not in {"", "PH", "LOA", "PL", "UL"}]
        if alternatives:
            source_day = min(alternatives, key=lambda day: abs(day - source_day))
            raw = str(assignments.get(str(source_day), "")).strip().upper()
        else:
            return "", "Source public-holiday marker was not carried into the target month."
    if raw == "WO":
        return "WO", ""
    if raw in LEAVE_ONLY_CODES:
        alternatives = [day for day in same_weekday if str(assignments.get(str(day), "")).strip().upper().split("/", 1)[0] in SHIFT_CODES | {"WO"}]
        if alternatives:
            source_day = min(alternatives, key=lambda day: abs(day - source_day))
            raw = str(assignments.get(str(source_day), "")).strip().upper().split("/", 1)[0]
            return raw, "Source leave marker was ignored; a recurring weekday pattern was used."
        return "", "Source leave marker was not used as target-month leave data; no recurring weekday pattern was available."
    if raw == "EXIT":
        return "", "Source Exit marker needs workforce status confirmation; no shifts were assigned."
    if "/" in raw:
        shift, suffix = (part.strip() for part in raw.split("/", 1))
        if shift in SHIFT_CODES:
            return shift, f"Source suffix /{suffix} was omitted because it may represent date-specific leave."
    if raw in SHIFT_CODES:
        return raw, ""
    if raw == "PH":
        return "", "Source public-holiday marker was not carried into the target month."
    if raw:
        return "", f"Unrecognized or non-shift source marker {raw!r} needs review."
    return "", ""


def _validate_employee(row, target_month, assignments):
    issues = []
    name = row.get("employee", row.get("name", "Unknown employee"))
    sheet = row.get("sheet", "")
    active_streak = int(row.get("prior_month_work_streak", 0))
    previous_code = row.get("prior_month_last_code", "")
    blocked_dates = set(row.get("blocked_dates", []))
    week_work = {}
    for index, code in enumerate(assignments, start=1):
        current_day = date(target_month.year, target_month.month, index)
        if code in SHIFT_CODES and current_day.isoformat() in blocked_dates:
            issues.append({"severity": "error", "code": "planned_leave_conflict", "sheet": sheet, "employee": name, "date": current_day.isoformat(), "message": f"{name} has a shift on a planned leave or blocked adjacent date."})
        if code in SHIFT_CODES and code not in _facility_allowed(sheet, row.get("activity", ""), current_day):
            issues.append({"severity": "error", "code": "facility_shift_unavailable", "sheet": sheet, "employee": name, "date": current_day.isoformat(), "message": f"Shift {code} is not available for {sheet} on this date."})
        if code in SHIFT_CODES:
            active_streak += 1
            week_key = current_day.isocalendar()[:2]
            week_work[week_key] = week_work.get(week_key, 0) + 1
        else:
            active_streak = 0
        if active_streak > 5:
            issues.append({"severity": "error", "code": "too_many_consecutive_days", "sheet": sheet, "employee": name, "date": current_day.isoformat(), "message": f"{name} worked more than five consecutive days."})
        if previous_code == "3" and code in {"G", "1", "2"}:
            issues.append({"severity": "error", "code": "shift_next_day_off", "sheet": sheet, "employee": name, "date": current_day.isoformat(), "message": f"{name} was assigned {code} after shift 3; a weekly off is required before returning to a day shift."})
        if code == "3" and row.get("prior_month_night_worked"):
            issues.append({"severity": "error", "code": "prior_month_night_restriction", "sheet": sheet, "employee": name, "date": current_day.isoformat(), "message": f"{name} worked night shift in the previous month and cannot be assigned another night shift this month."})
        previous_code = code
    for (year, week), worked in week_work.items():
        if worked > 6:
            issues.append({"severity": "error", "code": "weekly_off_missing", "sheet": sheet, "employee": name, "week": f"{year}-W{week:02d}", "message": f"{name} has no weekly off in ISO week {year}-W{week:02d}."})
    return issues


def create_roster_preview(workbook_import, target_month, selected_categories=None, public_holidays=None, staffing_requirements=None, history_complete=False):
    """Rotate shifts, apply supplied rules, and return a review draft."""
    target_month = date(target_month.year, target_month.month, 1)
    target_days = _month_days(target_month)
    selected = set(selected_categories or [])
    public_holidays = set(public_holidays or [])
    staffing_requirements = staffing_requirements or {}
    output_rows, warnings, categories, sheets_for_export = [], [], [], []
    employee_profiles = {
        employee.normalized_name: employee
        for employee in Employee.objects.filter(owner=workbook_import.uploaded_by)
    }
    excluded_rows = set(EmployeeAssignment.objects.filter(
        roster_sheet__workbook=workbook_import,
        excluded_from_roster=True,
    ).values_list("roster_sheet__name", "employee__normalized_name"))
    supported = [sheet for sheet in workbook_import.sheets.all() if sheet.layout_kind == "roster"]
    if selected:
        supported = [sheet for sheet in supported if sheet.name in selected]
    if not supported:
        return [], [{"severity": "error", "code": "no_roster_sheets", "message": "No roster worksheets matched the selected categories."}], [], []

    if not history_complete:
        warnings.append({"severity": "warning", "code": "annual_history_unavailable", "message": "Historical pattern records are incomplete for this target year, so the annual pattern-change limit could not be verified. This roster remains provisional."})
    if not staffing_requirements:
        warnings.append({"severity": "warning", "code": "staffing_targets_unconfigured", "message": "No saved or inferable staffing targets apply to this location and month. Coverage targets are unavailable, so this roster remains provisional."})

    for sheet in supported:
        categories.append(sheet.name)
        sheets_for_export.append({"name": sheet.name, "day_count": len(sheet.data_rows[0].get("day_columns", [])) if sheet.data_rows else 0})
        source_month = sheet.roster_month
        if not source_month:
            warnings.append({"severity": "warning", "code": "month_not_detected", "sheet": sheet.name, "message": f"Could not detect the source month for {sheet.name!r}; its rows were skipped."})
            continue
        source_day_count = len(sheet.data_rows[0].get("day_columns", [])) if sheet.data_rows else 0
        if target_days > source_day_count:
            warnings.append({"severity": "warning", "code": "missing_day_columns", "sheet": sheet.name, "message": f"{sheet.name!r} has {source_day_count} day columns, fewer than the {target_days} target-month days. The additional days cannot be written safely without changing this sheet's layout."})
        if source_month.month != target_month.month or source_month.year != target_month.year:
            warnings.append({"severity": "info", "code": "weekday_projection", "sheet": sheet.name, "message": f"Assignments for {sheet.name!r} are a weekday-pattern projection from {source_month:%B %Y}; they are not a confirmed business-rule generation."})

        planned_rows = []
        for row in sheet.data_rows:
            source_name = row.get("name", "")
            name_key = " ".join(str(source_name).casefold().split())
            profile = employee_profiles.get(name_key)
            employee_name = profile.display_name if profile else source_name
            is_active = profile.active if profile else True
            is_removed = (sheet.name, name_key) in excluded_rows
            previous_shift = _previous_shift(row)
            previous_shifts = _previous_shifts(row)
            plan_row = {
                "sheet": sheet.name,
                "employee": employee_name,
                "employee_id": profile.pk if profile else None,
                "employee_code": (profile.employee_code if profile and profile.employee_code else row.get("employee_code", "")),
                "employee_id_column": row.get("employee_id_column"),
                "previous_pattern": previous_shift,
                "active": is_active,
                "included": not is_removed and is_active,
                "activity": row.get("category", ""),
                "source_row": row.get("source_row"),
                "assignments": row.get("assignments", {}),
                "transport_preference": (profile.transport_preference if profile and profile.transport_preference else row.get("transport_preference", "")),
                "source_assignments": row.get("assignments", {}),
                "previous_shift": previous_shift,
                "previous_shifts": sorted(previous_shifts),
                "rotation_target": _rotated_shift(sheet.name, previous_shift, previous_shifts),
                "profile": profile,
                "excluded": is_removed or not is_active,
                "removed": is_removed,
                "source_month": source_month.isoformat(),
            }
            planned_rows.append(plan_row)
            if is_removed:
                warnings.append({"severity": "info", "code": "employee_removed", "sheet": sheet.name, "employee": employee_name, "message": f"{employee_name} was removed from this workbook's roster; no shifts were projected."})
            elif not is_active:
                warnings.append({"severity": "info", "code": "inactive_employee", "sheet": sheet.name, "employee": employee_name, "message": f"{employee_name} is inactive; no shifts were projected."})
            if previous_shift and not is_removed and is_active:
                warnings.append({"severity": "info", "code": "monthly_shift_rotation", "sheet": sheet.name, "employee": employee_name, "message": f"Previous primary shift {previous_shift}; this month's numbered assignments will use another shift."})
                if not plan_row["rotation_target"]:
                    warnings.append({"severity": "warning", "code": "rotation_requires_general", "sheet": sheet.name, "employee": employee_name, "message": f"{employee_name} worked every numbered shift in the source month; they will receive General or off rather than repeat one."})

        sheet_requirements = {key: value for key, value in staffing_requirements.items() if key[0] == sheet.name}
        sheet_rows, sheet_warnings = _build_sheet_assignments(sheet.name, planned_rows, source_month, target_month, public_holidays, sheet_requirements)
        output_rows.extend(sheet_rows)
        warnings.extend(sheet_warnings)
        comparable_days = min(_month_days(source_month), target_days)
        comparable_pairs = 0
        unchanged_pairs = 0
        for result_row, plan_row in zip(sheet_rows, planned_rows):
            source_codes = plan_row.get("source_assignments", {})
            for day_number in range(1, comparable_days + 1):
                old_code = str(source_codes.get(str(day_number), "")).strip().upper().split("/", 1)[0]
                new_code = result_row["assignments"][day_number - 1]
                if old_code in SHIFT_CODES | {"WO", "PH"}:
                    comparable_pairs += 1
                    unchanged_pairs += old_code == new_code
        if comparable_pairs:
            warnings.append({"severity": "info", "code": "roster_similarity", "sheet": sheet.name,
                "unchanged": unchanged_pairs, "comparable": comparable_pairs,
                "similarity_percent": round(100 * unchanged_pairs / comparable_pairs, 1),
                "message": f"Comparable employee-day assignments unchanged: {unchanged_pairs}/{comparable_pairs} ({round(100 * unchanged_pairs / comparable_pairs, 1)}%)."})
        for result_row in sheet_rows:
            warnings.extend(_validate_employee(result_row, target_month, result_row["assignments"]))
            prior = next((item.get("previous_shifts", []) for item in planned_rows if item["source_row"] == result_row["source_row"]), [])
            repeated = [code for code in result_row["assignments"] if code == "3" and code in prior]
            if repeated:
                warnings.append({"severity": "error", "code": "prior_month_night_repeated", "sheet": sheet.name, "employee": result_row["employee"], "message": f"{result_row['employee']} was assigned night shift despite working it in the source month."})

    assignments_by_employee_day = defaultdict(list)
    for row in output_rows:
        name_key = " ".join(str(row.get("employee", "")).casefold().split())
        for day_index, code in enumerate(row.get("assignments", []), start=1):
            if code in SHIFT_CODES:
                current_day = date(target_month.year, target_month.month, day_index)
                assignments_by_employee_day[(name_key, current_day)].append((row.get("employee"), row.get("sheet"), code))
    for (_, current_day), items in assignments_by_employee_day.items():
        if len(items) > 1:
            employee_name = items[0][0]
            details = ", ".join(f"{sheet}:{code}" for _, sheet, code in items)
            warnings.append({"severity": "error", "code": "conflicting_assignment", "employee": employee_name, "date": current_day.isoformat(), "message": f"{employee_name} has multiple assignments on {current_day:%d %b}: {details}."})

    if history_complete:
        seen_employees = set()
        for row in output_rows:
            employee_id = row.get("employee_id")
            if not employee_id or employee_id in seen_employees:
                continue
            seen_employees.add(employee_id)
            new_pattern = _previous_shift({"assignments": {str(i): code for i, code in enumerate(row.get("assignments", []), start=1)}})
            old_pattern = row.get("previous_pattern", "")
            profile = Employee.objects.filter(pk=employee_id).first()
            if not profile:
                continue
            snapshot = HistoricalRosterSnapshot.objects.filter(employee=profile, roster_month__lt=target_month).order_by("-roster_month").first()
            if snapshot and snapshot.pattern:
                old_pattern = snapshot.pattern
            else:
                prior_run = GenerationRun.objects.filter(created_by=workbook_import.uploaded_by, status="approved", target_month__lt=target_month).order_by("-target_month").first()
                if prior_run:
                    prior_row = next((item for item in prior_run.assignments if item.get("employee_id") == employee_id), None)
                    if prior_row:
                        old_pattern = _previous_shift({"assignments": {str(i): code for i, code in enumerate(prior_row.get("assignments", []), start=1)}}) or old_pattern
            if not new_pattern:
                new_pattern = old_pattern
            exceeds, changes = would_exceed_annual_pattern_limit(profile, old_pattern, new_pattern, target_month)
            if exceeds:
                warnings.append({"severity": "error", "code": "annual_pattern_change_limit", "employee": row.get("employee"), "date": target_month.isoformat(), "message": f"{row.get('employee')} would exceed two verified pattern changes in {target_month.year}; current count is {changes}."})

    if not any(sheet.name == "OB & CE" for sheet in supported):
        warnings.append({"severity": "info", "code": "attendance_not_projected", "message": "Attendance-only sheet 'OB & CE' is retained as a reference and is not projected as a shift roster."})
    warnings.append({"severity": "info", "code": "transport_soft_preference", "message": "Eligible own-vehicle employees are preferred when the scheduler fills a required shift; cab-dependent employees are used when needed."})
    warnings.append({"severity": "info", "code": "manual_review_required", "message": "Review the validation report and assignment summary before approval."})
    return output_rows, warnings, categories, sheets_for_export

"""Read the roster workbook without expanding its formatting-only rows."""
from __future__ import annotations

import calendar
import re
import zipfile
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.datetime import from_excel


MAX_WORKSHEETS = 25
MAX_DATA_ROWS = 3_000
MAX_EMPTY_ROW_GAP = 75
MAX_COLUMNS = 52
MAX_EXPANDED_BYTES = 300 * 1024 * 1024
NON_EMPLOYEE_ROWS = {"1st shift", "2nd shift", "3rd shift", "general shift", "total", "total shifts"}
COMBINED_FACILITY_TEAM_RE = re.compile(r"^\s*(HDC\s*\d+)\s*(?:[-–]\s*|\s+)(.+?)\s*$", re.IGNORECASE)


class WorkbookImportError(ValueError):
    """The supplied file is not a supported roster workbook."""


def _text(value):
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value).strip()


def _month_from(value, epoch):
    if isinstance(value, datetime):
        return date(value.year, value.month, 1)
    if isinstance(value, date):
        return date(value.year, value.month, 1)
    if isinstance(value, (int, float)):
        try:
            converted = from_excel(value, epoch)
            if isinstance(converted, (date, datetime)):
                return date(converted.year, converted.month, 1)
        except (TypeError, ValueError, OverflowError):
            return None
    raw = _text(value)
    match = re.search(r"([A-Za-z]{3,9})\W*(\d{2,4})", raw)
    if match:
        month_key = match.group(1).lower()[:3]
        month = next((i for i in range(1, 13) if calendar.month_abbr[i].lower() == month_key), None)
        if month:
            year = int(match.group(2))
            if year < 100:
                year += 2000 if year < 70 else 1900
            return date(year, month, 1)
    return None


def _is_day_header(value, expected):
    return isinstance(value, (int, float)) and int(value) == expected


def _split_facility_team(value):
    """Split source values such as ``HDC3- CS`` into location and team."""
    match = COMBINED_FACILITY_TEAM_RE.match(_text(value))
    if not match:
        return None
    facility = re.sub(r"\s+", "", match.group(1)).upper()
    return facility, match.group(2).strip()


def _detect_month_and_day_columns(rows, epoch):
    if not rows:
        return None, [], []
    first = rows[0]
    second = rows[1] if len(rows) > 1 else []
    month = next((_month_from(v, epoch) for v in first[:3] if v is not None and _month_from(v, epoch)), None)
    days = []
    headers = []
    for start in range(min(len(first), MAX_COLUMNS)):
        candidate = []
        expected = 1
        for index in range(start, min(len(first), MAX_COLUMNS)):
            if not _is_day_header(first[index], expected):
                break
            candidate.append(index)
            expected += 1
        if len(candidate) > len(days):
            days = candidate
            headers = [_text(second[index]) if index < len(second) else "" for index in candidate]
    return month, days, headers


def _reconcile_month_with_weekdays(month, day_columns, weekday_headers):
    """Correct a stale month label only when the full day grid proves another month."""
    if not month or len(day_columns) < 28 or len(weekday_headers) != len(day_columns):
        return month, False
    weekday_numbers = {calendar.day_abbr[index].casefold(): index for index in range(7)}
    observed = [weekday_numbers.get(str(value).strip()[:3].casefold()) for value in weekday_headers]
    if any(value is None for value in observed):
        return month, False
    candidates = []
    month_index = month.year * 12 + month.month - 1
    for offset in range(-2, 3):
        candidate_index = month_index + offset
        year, zero_month = divmod(candidate_index, 12)
        candidate_month = zero_month + 1
        candidate = date(year, candidate_month, 1)
        if calendar.monthrange(year, candidate_month)[1] != len(day_columns):
            continue
        if all(date(year, candidate_month, day).weekday() == weekday for day, weekday in enumerate(observed, start=1)):
            candidates.append((abs(offset), candidate))
    if not candidates:
        return month, False
    best_distance = min(distance for distance, _ in candidates)
    best = {candidate for distance, candidate in candidates if distance == best_distance}
    if len(best) != 1:
        return month, False
    inferred = next(iter(best))
    return inferred, inferred != month


def parse_workbook(uploaded_file):
    """Return bounded JSON-ready sheet data and validation warnings.

    The HDC3 worksheet advertises almost a million rows due to formatting. We
    reset read-only dimensions and stop after a long run of empty rows, so that
    formatting-only XML doesn't turn into a million-row Python iteration.
    """
    name = Path(getattr(uploaded_file, "name", "roster.xlsx")).name
    if Path(name).suffix.lower() not in {".xlsx", ".xlsm"}:
        raise WorkbookImportError("Upload an .xlsx or .xlsm Excel workbook.")
    try:
        uploaded_file.seek(0)
        with zipfile.ZipFile(uploaded_file) as package:
            members = package.infolist()
            if len(members) > 1_000 or sum(item.file_size for item in members) > MAX_EXPANDED_BYTES:
                raise WorkbookImportError("This workbook expands beyond the safe inspection limits.")
            if "xl/workbook.xml" not in package.namelist():
                raise WorkbookImportError("This file is not a valid Excel workbook package.")
        uploaded_file.seek(0)
        wb = load_workbook(uploaded_file, read_only=True, data_only=False, keep_vba=Path(name).suffix.lower() == ".xlsm")
    except WorkbookImportError:
        raise
    except (zipfile.BadZipFile, OSError) as exc:
        raise WorkbookImportError("Excel could not read this workbook. Check that it is a valid .xlsx or .xlsm file.") from exc
    except Exception as exc:  # openpyxl errors differ by invalid workbook type
        raise WorkbookImportError("Excel could not read this workbook. Check that it is a valid .xlsx or .xlsm file.") from exc

    if len(wb.worksheets) > MAX_WORKSHEETS:
        wb.close()
        raise WorkbookImportError(f"This workbook has more than {MAX_WORKSHEETS} worksheets.")

    summaries, parsed_sheets, warnings, leave_notes = [], [], [], []
    employee_keys = set()
    months_by_sheet = {}
    try:
        for ws in wb.worksheets:
            try:
                ws.reset_dimensions()
            except AttributeError:
                pass
            rows = []
            empty_gap = 0
            for row_index, cells in enumerate(ws.iter_rows(max_col=MAX_COLUMNS, values_only=False), start=1):
                values = [cell.value for cell in cells]
                has_value = any(value is not None for value in values)
                if has_value:
                    empty_gap = 0
                    if len(rows) < MAX_DATA_ROWS:
                        rows.append({"row_number": row_index, "values": values})
                else:
                    empty_gap += 1
                    if empty_gap >= MAX_EMPTY_ROW_GAP:
                        break
                if row_index >= MAX_DATA_ROWS:
                    warnings.append(f"{ws.title!r}: import stopped at the {MAX_DATA_ROWS:,}-row safety limit.")
                    break

            values_rows = [record["values"] for record in rows]
            month, day_columns, weekday_headers = _detect_month_and_day_columns(values_rows, wb.epoch)
            month, month_inferred = _reconcile_month_with_weekdays(month, day_columns, weekday_headers)
            first_row = values_rows[0] if values_rows else []
            header_row = values_rows[1] if len(values_rows) > 1 else []
            category_header = _text(header_row[1]) if len(header_row) > 1 else ""
            has_day_grid = len(day_columns) >= 20 and len(rows) >= 3
            is_leave_sheet = ws.title.strip().lower() == "leave details"
            is_base_sheet = ws.title.strip().lower() == "base file"
            is_attendance = ws.title.strip().lower() == "ob & ce"
            layout = "leave_notes" if is_leave_sheet else "staffing_reference" if is_base_sheet else "attendance" if is_attendance else "roster" if has_day_grid else "reference"

            header_labels = [_text(value) for value in (first_row if layout == "staffing_reference" else header_row) if value is not None]
            sheet_rows = []
            normalized_headers = [_text(value).casefold().replace("_", " ").strip() for value in header_row]
            facility_col = next((index for index, value in enumerate(normalized_headers) if value in {"facility", "location", "site", "center", "centre", "facility name", "location name"}), None)
            employee_col = next((index for index, value in enumerate(normalized_headers) if value in {"employee", "employee name", "resource", "resource name", "engineer", "engineer name", "name"}), None)
            activity_col = next((index for index, value in enumerate(normalized_headers) if value in {"activity", "team", "category", "department", "group"}), None)
            inferred_combined_location = False
            if layout == "roster" and facility_col is None and employee_col == 0 and len(header_row) > 1:
                combined_values = [
                    _split_facility_team(record["values"][1])
                    for record in rows[2:]
                    if len(record["values"]) > 1 and _text(record["values"][0])
                ]
                populated = [item for item in combined_values if item]
                if populated and len(populated) / max(1, sum(bool(_text(record["values"][0])) for record in rows[2:])) >= 0.8:
                    facility_col = 1
                    inferred_combined_location = True
            # On a facility-named worksheet, a column called "Facility" often
            # contains the service/team (for example Incidents or HDC4 Asset),
            # not a second location dimension. Keep the worksheet location and
            # use that column as the employee category.
            if layout == "roster" and facility_col is not None and not inferred_combined_location and re.fullmatch(r"HDC\s*\d+", ws.title.strip(), re.IGNORECASE):
                if activity_col is None or activity_col == facility_col:
                    activity_col = facility_col
                facility_col = None
            consolidated_groups = {}
            if layout == "roster" and facility_col is not None:
                if employee_col is None or employee_col == facility_col:
                    layout = "reference"
                    warnings.append(f"{ws.title!r}: a location column was found, but a separate Employee Name/Resource column is required for automatic roster generation.")
                else:
                    facility_values = set()
                    for record in rows[2:]:
                        if facility_col >= len(record["values"]):
                            continue
                        raw_value = _text(record["values"][facility_col])
                        if not raw_value:
                            continue
                        split_value = _split_facility_team(raw_value) if inferred_combined_location else None
                        facility_values.add((split_value[0] if split_value else raw_value).casefold())
                    is_single_location_tab = len(facility_values) == 1 and next(iter(facility_values)) == ws.title.strip().casefold()
                    if not is_single_location_tab:
                        layout = "consolidated_source"
                if layout == "consolidated_source":
                    for record in rows[2:]:
                        values = record["values"]
                        raw_facility = _text(values[facility_col] if facility_col < len(values) else None)
                        split_value = _split_facility_team(raw_facility) if inferred_combined_location else None
                        facility = split_value[0] if split_value else raw_facility
                        employee_name = _text(values[employee_col] if employee_col < len(values) else None)
                        if not facility or not employee_name or employee_name.casefold() in NON_EMPLOYEE_ROWS:
                            continue
                        assignments = {str(day): _text(values[column] if column < len(values) else None) for day, column in enumerate(day_columns, 1)}
                        employee_code_col = next((index for index, value in enumerate(normalized_headers) if value in {"employee id", "emp id", "employee code", "emp code"}), None)
                        transport_col = next((index for index, value in enumerate(normalized_headers) if value in {"own/cab", "transport", "transport preference"}), None)
                        row_data = {
                            "source_row": record["row_number"], "name": employee_name,
                            "source_tab": ws.title,
                            "category": split_value[1] if split_value else _text(values[activity_col] if activity_col is not None and activity_col < len(values) else ""),
                            "category_header": _text(header_row[activity_col] if activity_col is not None and activity_col < len(header_row) else "Activity"),
                            "assignments": assignments,
                            "day_columns": [get_column_letter(i + 1) for i in day_columns],
                            "rollups": {},
                            "transport_preference": _text(values[transport_col]) if transport_col is not None and transport_col < len(values) else "",
                            "employee_code": _text(values[employee_code_col]) if employee_code_col is not None and employee_code_col < len(values) else "",
                            "employee_id_column": employee_code_col,
                        }
                        consolidated_groups.setdefault(facility, []).append(row_data)
                        employee_keys.add(employee_name.casefold())
            records_to_process = rows if is_leave_sheet or layout == "staffing_reference" else rows[2:]
            for record in records_to_process:
                row_index = record["row_number"]
                values = record["values"]
                if is_leave_sheet:
                    leave_name = _text(values[5] if len(values) > 5 else None)
                    raw_date = _text(values[6] if len(values) > 6 else None)
                    raw_note = _text(values[7] if len(values) > 7 else None)
                    if leave_name and (raw_date or raw_note):
                        leave_notes.append({"employee_name": leave_name, "raw_date_text": raw_date, "raw_note": raw_note})
                    continue
                if layout == "staffing_reference":
                    if any(value is not None for value in values):
                        sheet_rows.append({"source_row": row_index, "values": [_text(value) for value in values], "cells": [{"column": get_column_letter(i + 1), "value": _text(value)} for i, value in enumerate(values) if value is not None]})
                    continue
                if layout == "consolidated_source":
                    continue
                name_value = _text(values[0] if values else None)
                if not name_value or name_value.casefold() in NON_EMPLOYEE_ROWS:
                    continue
                # The Base file and leave sheet use non-roster table layouts.
                if layout in {"leave_notes", "staffing_reference", "reference"}:
                    continue
                category = _text(values[1] if len(values) > 1 else None)
                assignments = {}
                for day_index, column_index in enumerate(day_columns, start=1):
                    raw = values[column_index] if column_index < len(values) else None
                    assignments[str(day_index)] = _text(raw)
                rollups = {}
                for col_index in range(33, min(len(values), MAX_COLUMNS)):
                    header = _text(header_row[col_index] if col_index < len(header_row) else None)
                    value = values[col_index]
                    if header and value is not None:
                        rollups[header] = _text(value)
                identity_column = next((i for i, label in enumerate(header_row) if _text(label).casefold() in {"employee id", "emp id", "employee code", "emp code"}), None)
                row_data = {
                    "source_row": row_index,
                    "source_tab": ws.title,
                    "name": name_value,
                    "category": category,
                    "category_header": category_header,
                    "assignments": assignments,
                    "day_columns": [get_column_letter(i + 1) for i in day_columns],
                    "rollups": rollups,
                    "transport_preference": next((value for key, value in rollups.items() if key.strip().casefold() in {"own/cab", "transport", "transport preference"}), ""),
                    "employee_code": _text(values[identity_column]) if identity_column is not None and identity_column < len(values) else "",
                    "employee_id_column": identity_column,
                }
                sheet_rows.append(row_data)
                employee_keys.add(name_value.casefold())

            months_by_sheet[ws.title] = month.isoformat() if month else None
            sheet_meta = {
                "name": ws.title,
                "hidden": ws.sheet_state != "visible",
                "layout_kind": layout,
                "month": month.isoformat() if month else None,
                "month_inferred": month_inferred,
                "day_count": len(day_columns),
                "employee_rows": sum(bool(item.get("name")) for item in sheet_rows),
                "reference_rows": len(sheet_rows) if layout == "staffing_reference" else 0,
                "header_fields": header_labels,
            }
            summaries.append(sheet_meta)
            parsed_sheets.append({**sheet_meta, "rows": sheet_rows, "weekdays": weekday_headers})

            # A consolidated input has one source worksheet but behaves as a
            # set of facility rosters everywhere downstream. Keep the original
            # worksheet in the uploaded file and create virtual facility tabs.
            for facility, facility_rows in consolidated_groups.items():
                facility_meta = {
                    "name": facility, "hidden": False, "layout_kind": "roster",
                    "month": month.isoformat() if month else None,
                    "day_count": len(day_columns), "employee_rows": len(facility_rows),
                    "reference_rows": 0, "header_fields": header_labels,
                    "consolidated": True, "source_sheet_name": ws.title,
                }
                matching_sheet = next((
                    item for item in parsed_sheets
                    if item["layout_kind"] == "roster"
                    and item["name"] == facility
                    and item.get("month") == facility_meta["month"]
                ), None)
                if matching_sheet:
                    matching_sheet["rows"].extend(facility_rows)
                    matching_sheet["employee_rows"] = len(matching_sheet["rows"])
                    matching_sheet["consolidated"] = True
                    for summary in summaries:
                        if summary["layout_kind"] == "roster" and summary["name"] == facility and summary.get("month") == facility_meta["month"]:
                            summary["employee_rows"] = matching_sheet["employee_rows"]
                            summary["consolidated"] = True
                            break
                    continue
                summaries.append(facility_meta)
                parsed_sheets.append({**facility_meta, "rows": facility_rows, "weekdays": weekday_headers})

            if is_leave_sheet:
                warnings.append("Leave details uses free-text date ranges and notes; review entries before using them as availability.")
            if month_inferred:
                warnings.append(f"{ws.title!r}: the month label did not match the complete weekday/day grid; source month was inferred as {month:%B %Y}.")
            if layout == "staffing_reference":
                warnings.append("Base file contains activity/count tables with no unambiguous date and shift mapping; automation will treat them as reference data and infer provisional targets from actual assignments.")

        distinct_months = sorted({value for value in months_by_sheet.values() if value})
        if len(distinct_months) > 1:
            warnings.append("The workbook contains more than one roster month: " + ", ".join(distinct_months) + ".")
        names = [sheet["name"] for sheet in summaries]
        if len(set(names)) != len(names):
            warnings.append("Duplicate worksheet names were found; use exact names when referring to tabs.")
        if "DD" in names and "DD " in names:
            warnings.append("The workbook has separate 'DD' and 'DD ' worksheets. The second name contains a trailing space.")
        if employee_keys:
            warnings.append("No dedicated employee ID field was detected; the import matched employees by normalized name only.")
        shift_codes = sorted({value for sheet in parsed_sheets for row in sheet["rows"] for value in row.get("assignments", {}).values() if value})
        return {
            "sheet_summary": summaries,
            "sheets": parsed_sheets,
            "leave_notes": leave_notes,
            "warnings": warnings,
            "shift_codes": shift_codes,
            "employee_count": len(employee_keys),
        }
    finally:
        wb.close()
        uploaded_file.seek(0)


def save_import_records(workbook_import, parsed):
    """Persist workbook sheets and unique names using the Django ORM."""
    from planner.models import Activity, Employee, EmployeeAssignment, LeaveNote, RosterSheet, ShiftCode

    sheet_by_name = {}
    for sheet_info in parsed["sheets"]:
        month = date.fromisoformat(sheet_info["month"]) if sheet_info.get("month") else None
        sheet = RosterSheet.objects.create(
            workbook=workbook_import,
            name=sheet_info["name"],
            hidden=sheet_info["hidden"],
            roster_month=month,
            layout_kind=sheet_info["layout_kind"],
            source_headers=sheet_info["header_fields"],
            row_count=sheet_info["employee_rows"],
            data_rows=sheet_info["rows"],
        )
        sheet_by_name[sheet.name] = sheet
        for row_index, row in enumerate(sheet_info["rows"], start=1):
            if not row.get("name"):
                continue
            normalized = " ".join(row["name"].casefold().split())
            employee, _ = Employee.objects.get_or_create(
                owner=workbook_import.uploaded_by,
                normalized_name=normalized,
                defaults={"display_name": row["name"]},
            )
            if row.get("employee_code") and not employee.employee_code:
                employee.employee_code = row["employee_code"]
                employee.save(update_fields=["employee_code"])
            if row.get("transport_preference") and not employee.transport_preference:
                employee.transport_preference = row["transport_preference"]
                employee.save(update_fields=["transport_preference"])
            activity = None
            if row["category"] and "activit" in row["category_header"].casefold():
                activity, _ = Activity.objects.get_or_create(name=row["category"])
            EmployeeAssignment.objects.get_or_create(
                employee=employee,
                roster_sheet=sheet,
                defaults={"activity": activity, "category_label": row["category"], "row_order": row_index},
            )
    # Raw leave lines remain review-only; exact dates are not silently parsed.
    leave_summary = next((item for item in parsed["sheet_summary"] if item["layout_kind"] == "leave_notes"), None)
    if leave_summary:
        for row in parsed.get("leave_notes", []):
            LeaveNote.objects.create(workbook=workbook_import, **row)
    for code in parsed.get("shift_codes", []):
        ShiftCode.objects.get_or_create(code=code)

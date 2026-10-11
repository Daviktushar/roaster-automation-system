from collections import Counter
from datetime import date

from django.db import transaction

from planner.models import Employee, EmployeePatternChange, HistoricalRosterSnapshot, PatternHistoryBaseline


def _pattern(assignments):
    values = [str(value).strip().upper().split("/", 1)[0] for value in assignments.values()]
    values = [value for value in values if value in {"1", "2", "3"}]
    if not values:
        return ""
    counts = Counter(values)
    return max(counts, key=lambda code: (counts[code], max(i for i, value in enumerate(values) if value == code)))


def import_workbook_history(workbook_import):
    """Persist every roster row with an identifiable month; never infer missing months."""
    created = 0
    with transaction.atomic():
        for sheet in workbook_import.sheets.filter(layout_kind="roster").exclude(roster_month__isnull=True):
            for row in sheet.data_rows:
                name = str(row.get("name", "")).strip()
                key = " ".join(name.casefold().split())
                if not key:
                    continue
                employee = Employee.objects.filter(owner=workbook_import.uploaded_by, normalized_name=key).first()
                _, was_created = HistoricalRosterSnapshot.objects.update_or_create(
                    roster_sheet=sheet,
                    employee_key=key,
                    defaults={
                        "owner": workbook_import.uploaded_by,
                        "workbook": workbook_import,
                        "employee": employee,
                        "employee_name": name,
                        "roster_month": sheet.roster_month,
                        "assignments": row.get("assignments", {}),
                        "pattern": _pattern(row.get("assignments", {})),
                        "transport_preference": row.get("transport_preference", ""),
                    },
                )
                created += int(was_created)

        snapshots = list(HistoricalRosterSnapshot.objects.filter(owner=workbook_import.uploaded_by).order_by("employee_key", "roster_month"))
        for employee_key in {item.employee_key for item in snapshots}:
            history = [item for item in snapshots if item.employee_key == employee_key and item.pattern]
            for previous, current in zip(history, history[1:]):
                consecutive = (current.roster_month.year - previous.roster_month.year) * 12 + current.roster_month.month - previous.roster_month.month == 1
                if consecutive and previous.pattern != current.pattern:
                    EmployeePatternChange.objects.get_or_create(
                        employee=current.employee,
                        effective_start=current.roster_month,
                        new_pattern=current.pattern,
                        defaults={"previous_pattern": previous.pattern, "changed_on": current.roster_month, "source": "imported_roster", "source_snapshot": current},
                    )
    return created


def history_status(owner, target_month):
    months = set(HistoricalRosterSnapshot.objects.filter(owner=owner).values_list("roster_month", flat=True))
    months.update(date(year, month, 1) for year, month in owner.generation_runs.filter(status="approved").values_list("target_month__year", "target_month__month"))
    months = sorted(months)
    gaps = []
    if len(months) > 1:
        known_months = set(months)
        cursor = months[0]
        while cursor < months[-1]:
            cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)
            if cursor not in known_months:
                gaps.append(cursor)
    baseline = PatternHistoryBaseline.objects.filter(owner=owner).first()
    year_start = date(target_month.year, 1, 1)
    complete = bool(baseline and baseline.effective_from <= year_start)
    if complete:
        expected = []
        cursor = year_start
        while cursor < target_month:
            expected.append(cursor)
            cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)
        known = set(months)
        # The baseline records where verified tracking begins; every later month
        # through the prior month still needs an imported or approved roster.
        complete = all(month in known for month in expected)
    return {"months": months, "missing_months": gaps, "baseline": baseline, "complete_for_target_year": complete}


def annual_pattern_changes(employee, target_month):
    return EmployeePatternChange.objects.filter(employee=employee, changed_on__year=target_month.year, verified=True).count()


def would_exceed_annual_pattern_limit(employee, previous_pattern, proposed_pattern, target_month):
    count = annual_pattern_changes(employee, target_month)
    return bool(previous_pattern and proposed_pattern and previous_pattern != proposed_pattern and count >= 2), count

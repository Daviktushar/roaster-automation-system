from datetime import date
from types import SimpleNamespace
import unittest
from collections import Counter

from planner.services.roster_generation import (
    _build_sheet_assignments,
    _fixed_coverage_target,
    _inferred_coverage_target,
    _leave_dates,
    _month_days,
    _pattern_code,
    _previous_shift,
    _previous_shifts,
    _required_shifts,
    _rotated_shift,
    _validate_employee,
)


class RosterProjectionTests(unittest.TestCase):
    def test_month_lengths_and_december_to_january_calendar_transition(self):
        self.assertEqual(_month_days(date(2024, 2, 1)), 29)
        self.assertEqual(_month_days(date(2025, 2, 1)), 28)
        self.assertEqual(_month_days(date(2026, 4, 1)), 30)
        self.assertEqual(_month_days(date(2026, 12, 1)), 31)
        source = date(2026, 12, 1)
        target = date(source.year + (source.month == 12), 1 if source.month == 12 else source.month + 1, 1)
        self.assertEqual(target, date(2027, 1, 1))

    def make_rows(self, sheet_name, counts):
        rows = []
        row_number = 3
        for previous_shift, count, category in counts:
            for _ in range(count):
                codes = {str(day): previous_shift for day in range(1, 32)}
                for day in range(28, 32):
                    codes[str(day)] = "WO"
                old_codes = {previous_shift} if previous_shift in {"1", "2", "3"} else set()
                rows.append({
                    "sheet": sheet_name,
                    "employee": f"Engineer {row_number}",
                    "active": True,
                    "included": True,
                    "activity": category,
                    "source_row": row_number,
                    "assignments": codes,
                    "source_assignments": codes,
                    "previous_shift": _previous_shift({"assignments": codes}),
                    "previous_shifts": sorted(old_codes),
                    "rotation_target": _rotated_shift(sheet_name, previous_shift if old_codes else None, old_codes),
                    "profile": None,
                    "excluded": False,
                    "removed": False,
                    "source_month": "2026-10-01",
                })
                row_number += 1
        return rows

    def test_hdc3_rotates_numbered_shifts_and_meets_daily_coverage(self):
        rows = self.make_rows("HDC3", [("1", 8, "Incidents"), ("2", 12, "Incidents"), ("3", 12, "Incidents"), ("G", 12, "Incidents")])
        output, warnings = _build_sheet_assignments("HDC3", rows, date(2026, 10, 1), date(2026, 11, 1), set())
        self.assertFalse([warning for warning in warnings if warning["severity"] == "error"])
        for day in range(1, 31):
            target = date(2026, 11, day)
            counts = Counter(row["assignments"][day - 1] for row in output)
            self.assertEqual(counts["1"], 1 if target.weekday() >= 5 else 0)
            self.assertGreaterEqual(counts["2"], 1)
            self.assertEqual(counts["3"], 1)
            self.assertGreaterEqual(counts["G"], 1 if target.weekday() < 5 else 0)
        for row in output:
            previous = next(item["previous_shifts"] for item in rows if item["source_row"] == row["source_row"])
            if "3" in previous:
                self.assertNotIn("3", row["assignments"])
            for index, code in enumerate(row["assignments"]):
                current = date(2026, 11, index + 1)
                if code == "3" and current.weekday() >= 5:
                    prior = row["assignments"][index - 1] if index else row["prior_month_last_code"]
                    following = row["assignments"][index + 1] if index + 1 < len(row["assignments"]) else "WO"
                    self.assertNotEqual(prior, "3")
                    self.assertEqual(following, "WO")
            for index, code in enumerate(row["assignments"][:-1]):
                if code == "3":
                    self.assertNotIn(row["assignments"][index + 1], {"1", "2", "G"})
            self.assertFalse(any(all(code in {"G", "1", "2", "3"} for code in row["assignments"][start:start + 6]) for start in range(25)))
        self.assertTrue(any(
            row["assignments"][day - 2] in {"G", "1", "2"}
            for row in output for day in range(2, 31)
            if date(2026, 11, day).weekday() >= 5 and row["assignments"][day - 1] == "3"
        ))

    def test_hdc4_and_hdc5_facility_rules_and_asset_weekends(self):
        hdc4_rows = self.make_rows("HDC4", [("1", 6, "Incidents"), ("2", 12, "Incidents"), ("G", 18, "HDC4 Asset")])
        hdc4, hdc4_warnings = _build_sheet_assignments("HDC4", hdc4_rows, date(2026, 10, 1), date(2026, 11, 1), set())
        hdc4_errors = [warning for warning in hdc4_warnings if warning["severity"] == "error"]
        self.assertTrue(hdc4_errors)
        self.assertTrue(all(warning["code"] == "staffing_shortage" for warning in hdc4_errors))
        for day in range(1, 31):
            target = date(2026, 11, day)
            counts = Counter(row["assignments"][day - 1] for row in hdc4)
            self.assertEqual(counts["1"], 1)
            self.assertGreaterEqual(counts["2"], 1)
            self.assertEqual(counts["3"], 0)
            if target.weekday() >= 5:
                self.assertEqual(counts["G"], 0)
                for row, source in zip(hdc4, hdc4_rows):
                    if "asset" in source["activity"].casefold():
                        self.assertIn(row["assignments"][day - 1], {"WO", ""})
        hdc5_rows = self.make_rows("HDC5", [("G", 8, "HDC5"), ("G", 3, "HDC5 Asset")])
        hdc5, _ = _build_sheet_assignments("HDC5", hdc5_rows, date(2026, 10, 1), date(2026, 11, 1), set())
        for day in range(1, 31):
            target = date(2026, 11, day)
            counts = Counter(row["assignments"][day - 1] for row in hdc5)
            self.assertEqual(sum(counts[code] for code in ("1", "2", "3")), 0)
            if target.weekday() >= 5:
                self.assertEqual(counts["G"], 0)
            else:
                self.assertGreaterEqual(counts["G"], 1)

    def test_leave_adjacent_to_holiday_and_weekend_is_blocked(self):
        profile = SimpleNamespace(planned_leave=[{"start": "2026-11-13", "end": "2026-11-13"}])
        holidays = {date(2026, 11, 12)}
        blocked = _leave_dates(profile, date(2026, 11, 1), holidays)
        self.assertIn(date(2026, 11, 12), blocked)
        self.assertIn(date(2026, 11, 13), blocked)
        self.assertIn(date(2026, 11, 14), blocked)
        self.assertIn(date(2026, 11, 15), blocked)

    def test_generator_applies_profile_leave_and_adjacent_holiday_weekend(self):
        rows = self.make_rows("HDC5", [("G", 8, "HDC5")])
        rows[0]["profile"] = SimpleNamespace(
            planned_leave=[{"start": "2026-11-12", "end": "2026-11-13"}]
        )
        holiday = date(2026, 11, 11)
        output, _ = _build_sheet_assignments(
            "HDC5", rows, date(2026, 10, 1), date(2026, 11, 1), {holiday}
        )
        blocked = {date.fromisoformat(value) for value in output[0]["blocked_dates"]}
        expected = {date(2026, 11, day) for day in range(11, 16)}
        self.assertTrue(expected.issubset(blocked))
        for day in range(11, 16):
            self.assertNotEqual(output[0]["assignments"][day - 1], "G")

    def test_required_shift_patterns_match_each_facility_rule(self):
        self.assertEqual(_required_shifts("HDC3", date(2026, 11, 2)), ["G", "2", "3"])
        self.assertEqual(_required_shifts("HDC3", date(2026, 11, 1)), ["1", "2", "3"])
        self.assertEqual(_required_shifts("HDC4", date(2026, 11, 2)), ["G", "1", "2"])
        self.assertEqual(_required_shifts("HDC4", date(2026, 11, 1)), ["1", "2"])
        self.assertEqual(_required_shifts("HDC5", date(2026, 11, 1)), [])
        self.assertEqual(_required_shifts("HDC5", date(2026, 11, 2)), ["G"])

    def test_fixed_counts_follow_shared_rules_and_other_counts_reuse_source_weekday(self):
        self.assertEqual(_fixed_coverage_target("HDC3", date(2026, 11, 2), "3"), 1)
        self.assertEqual(_fixed_coverage_target("HDC3", date(2026, 11, 1), "2"), 1)
        self.assertEqual(_fixed_coverage_target("HDC4", date(2026, 11, 2), "1"), 1)
        self.assertIsNone(_fixed_coverage_target("HDC3", date(2026, 11, 2), "2"))
        rows = self.make_rows("HDC3", [("G", 12, "Incidents")])
        self.assertEqual(_inferred_coverage_target(rows, date(2026, 10, 1), date(2026, 11, 2), "G"), 12)
    def test_same_weekday_pattern_projects_general_shift(self):
        row = {"assignments": {"5": "G", "12": "WO", "19": "2", "26": "G"}}
        code, note = _pattern_code(row, date(2026, 10, 1), date(2026, 11, 2))
        self.assertEqual(code, "G")
        self.assertEqual(note, "")

    def test_holiday_is_not_carried_to_target_month(self):
        row = {"assignments": {"5": "PH", "12": "PH", "19": "PH", "26": "PH"}}
        code, note = _pattern_code(row, date(2026, 10, 1), date(2026, 11, 2))
        self.assertNotEqual(code, "PH")
        self.assertTrue(note)

    def test_combined_leave_keeps_shift_as_flagged_draft(self):
        row = {"assignments": {"5": "G/PL", "12": "G", "19": "G", "26": "G"}}
        code, note = _pattern_code(row, date(2026, 10, 1), date(2026, 11, 2))
        self.assertEqual(code, "G")
        self.assertIn("leave", note)

    def test_more_than_five_consecutive_days_is_an_error(self):
        row = {"name": "Demo employee", "sheet": "HDC3"}
        assignments = ["G", "G", "1", "2", "3", "G", "WO"]
        issues = _validate_employee(row, date(2026, 11, 1), assignments)
        self.assertTrue(any(item["code"] == "too_many_consecutive_days" for item in issues))

    def test_third_shift_to_day_shift_requires_weekly_off(self):
        row = {"name": "Demo employee", "sheet": "HDC3"}
        issues = _validate_employee(row, date(2026, 11, 1), ["3", "2", "WO"])
        self.assertTrue(any(item["code"] == "shift_next_day_off" for item in issues))

    def test_third_shift_is_not_blocked_by_an_unrequested_pre_shift_off(self):
        row = {"name": "Demo employee", "sheet": "HDC3", "prior_month_last_code": "2"}
        issues = _validate_employee(row, date(2026, 11, 1), ["3", "WO"])
        self.assertFalse(any(item["code"] == "weekend_night_pre_rest" for item in issues))

    def test_shift_two_does_not_require_next_day_off(self):
        row = {"name": "Demo employee", "sheet": "HDC3"}
        issues = _validate_employee(row, date(2026, 11, 1), ["2", "G", "WO"])
        self.assertFalse(any(item["code"] == "shift_next_day_off" for item in issues))

    def test_previous_month_night_worker_is_not_reassigned_nights(self):
        rows = self.make_rows("HDC3", [("3", 6, "Incidents"), ("1", 12, "Incidents"), ("2", 12, "Incidents"), ("G", 12, "Incidents")])
        result, _ = _build_sheet_assignments("HDC3", rows, date(2026, 10, 1), date(2026, 11, 1), set())
        night_workers = {row["employee"] for row in result if "3" in row["assignments"]}
        prior_night_workers = {row["employee"] for row in rows if "3" in row["previous_shifts"]}
        self.assertFalse(night_workers & prior_night_workers)

    def test_own_vehicle_resource_is_preferred_for_required_single_shift(self):
        rows = self.make_rows("HDC4", [("G", 2, "Incidents")])
        rows[0]["transport_preference"] = "OWN"
        rows[1]["transport_preference"] = "CAB"
        result, _ = _build_sheet_assignments("HDC4", rows, date(2026, 10, 1), date(2026, 11, 1), set())
        sunday_shift_one = [row for row in result if row["assignments"][0] == "1"]
        self.assertEqual(len(sunday_shift_one), 1)
        self.assertEqual(sunday_shift_one[0]["transport_preference"], "OWN")

    def test_configured_staffing_count_is_filled_by_scheduler(self):
        rows = self.make_rows("HDC4", [("1", 8, "Incidents"), ("2", 16, "Incidents"), ("G", 20, "Incidents")])
        result, warnings = _build_sheet_assignments("HDC4", rows, date(2026, 10, 1), date(2026, 11, 1), set(), {("HDC4", "2", "weekday"): 3})
        monday = Counter(row["assignments"][1] for row in result)
        self.assertEqual(monday["2"], 3)
        self.assertFalse(any(item.get("date") == "2026-11-02" and item.get("code") == "staffing_shortage" for item in warnings))


if __name__ == "__main__":
    unittest.main()

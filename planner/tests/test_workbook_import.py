from datetime import date
from io import BytesIO
import unittest

from openpyxl import Workbook

from planner.services.workbook_import import parse_workbook


class NamedUpload(BytesIO):
    name = "sample.xlsx"


class WorkbookImportTests(unittest.TestCase):
    def test_month_day_codes_and_exact_trailing_space_are_detected(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "DD "
        sheet["A1"] = "Oct'26"
        sheet["B1"] = "DD"
        sheet["A2"] = "EMPLOYEE NAME"
        sheet["B2"] = "Activity"
        for day in range(1, 32):
            sheet.cell(1, day + 2, day)
            sheet.cell(2, day + 2, "Thu")
        sheet["A3"] = "Demo employee"
        sheet["B3"] = "META"
        sheet["C3"] = "G"
        sheet["D3"] = "WO"
        source = NamedUpload()
        workbook.save(source)
        source.seek(0)
        result = parse_workbook(source)
        self.assertEqual(result["sheet_summary"][0]["name"], "DD ")
        self.assertEqual(result["sheet_summary"][0]["month"], date(2026, 10, 1).isoformat())
        self.assertEqual(result["sheets"][0]["rows"][0]["assignments"]["1"], "G")
        self.assertEqual(result["sheets"][0]["rows"][0]["source_row"], 3)

    def test_non_excel_extension_is_rejected(self):
        file = NamedUpload()
        file.name = "roster.csv"
        with self.assertRaises(ValueError):
            parse_workbook(file)

    def test_corrects_stale_month_label_when_day_count_and_all_weekdays_agree(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "HDC5"
        sheet["A1"] = "Sep'26"
        sheet["A2"] = "EMPLOYEE NAME"
        for day in range(1, 32):
            sheet.cell(1, day + 2, day)
            sheet.cell(2, day + 2, date(2026, 10, day).strftime("%a"))
        sheet["A3"] = "Demo employee"
        sheet["B3"] = "HDC5"
        source = NamedUpload()
        workbook.save(source)
        source.seek(0)

        result = parse_workbook(source)
        self.assertEqual(result["sheet_summary"][0]["month"], date(2026, 10, 1).isoformat())
        self.assertTrue(result["sheet_summary"][0]["month_inferred"])

    def test_ambiguous_base_file_rows_are_retained_for_admin_confirmation(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Base file"
        sheet["H1"] = "2nd Shift"
        sheet["I1"] = "HDC3"
        sheet["J1"] = "HDC4"
        sheet["H2"] = 3
        sheet["I2"] = 4
        sheet["J2"] = 2
        source = NamedUpload()
        workbook.save(source)
        source.seek(0)
        result = parse_workbook(source)
        base = result["sheets"][0]
        self.assertEqual(base["layout_kind"], "staffing_reference")
        self.assertEqual(base["rows"][0]["cells"], [
            {"column": "H", "value": "2nd Shift"},
            {"column": "I", "value": "HDC3"},
            {"column": "J", "value": "HDC4"},
        ])

    def test_infers_hdc_locations_and_teams_from_combined_second_column(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Roster"
        sheet["A1"] = "Oct'26"
        sheet["A2"] = "EMPLOYEE NAME"
        for day in range(1, 32):
            sheet.cell(1, day + 2, day)
            sheet.cell(2, day + 2, date(2026, 10, day).strftime("%a"))
        for row_number, name, location_team in (
            (3, "Alex", "HDC3- CS"),
            (4, "Blair", "HDC3- Asset"),
            (5, "Casey", "HDC5- CS"),
            (6, "Drew", "HDC5 Asset"),
        ):
            sheet.cell(row_number, 1, name)
            sheet.cell(row_number, 2, location_team)
            for day in range(1, 32):
                sheet.cell(row_number, day + 2, "WO" if date(2026, 10, day).weekday() >= 5 else "G")
        source = NamedUpload()
        workbook.save(source)
        source.seek(0)

        result = parse_workbook(source)
        locations = {item["name"]: item for item in result["sheet_summary"] if item.get("consolidated")}
        self.assertEqual(set(locations), {"HDC3", "HDC5"})
        rows = {item["name"]: item["rows"] for item in result["sheets"] if item.get("consolidated")}
        self.assertEqual(rows["HDC3"][0]["category"], "CS")
        self.assertEqual(rows["HDC3"][1]["category"], "Asset")
        self.assertEqual(rows["HDC5"][0]["category"], "CS")
        self.assertEqual(rows["HDC5"][1]["category"], "Asset")

        single_location = Workbook()
        single_sheet = single_location.active
        single_sheet.title = "HDC3"
        single_sheet["A1"] = "Oct'26"
        single_sheet["A2"] = "EMPLOYEE NAME"
        for day in range(1, 32):
            single_sheet.cell(1, day + 2, day)
            single_sheet.cell(2, day + 2, date(2026, 10, day).strftime("%a"))
        single_sheet["A3"] = "Alex"
        single_sheet["B3"] = "HDC3- CS"
        single_source = NamedUpload()
        single_location.save(single_source)
        single_source.seek(0)
        single_result = parse_workbook(single_source)
        roster_tabs = [item for item in single_result["sheet_summary"] if item["layout_kind"] == "roster"]
        self.assertEqual([item["name"] for item in roster_tabs], ["HDC3"])

    def test_facility_named_tabs_keep_facility_column_as_team_category(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "HDC3"
        sheet["A1"] = "Oct'26"
        sheet["B1"] = "HDC3"
        sheet["A2"] = "EMPLOYEE NAME"
        sheet["B2"] = "Facility"
        for day in range(1, 32):
            sheet.cell(1, day + 2, day)
            sheet.cell(2, day + 2, date(2026, 10, day).strftime("%a"))
        sheet["A3"] = "Alex"
        sheet["B3"] = "Incidents"
        sheet["A4"] = "Blair"
        sheet["B4"] = "HDC3 Asset"
        source = NamedUpload()
        workbook.save(source)
        source.seek(0)

        result = parse_workbook(source)
        rosters = [item for item in result["sheets"] if item["layout_kind"] == "roster"]
        self.assertEqual(len(rosters), 1)
        self.assertEqual(rosters[0]["name"], "HDC3")
        self.assertEqual([row["category"] for row in rosters[0]["rows"]], ["Incidents", "HDC3 Asset"])


if __name__ == "__main__":
    unittest.main()

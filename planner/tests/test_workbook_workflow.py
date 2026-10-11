from datetime import date
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook, load_workbook

from planner.models import Employee, EmployeeAssignment, GenerationRun, PublicHoliday, WorkbookImport


def make_reference_workbook():
    book = Workbook()
    sheet = book.active
    sheet.title = "HDC3"
    sheet["A1"] = "Oct'26"
    sheet["B1"] = "HDC3"
    sheet["A2"] = "EMPLOYEE NAME"
    sheet["B2"] = "Activity"
    for day in range(1, 32):
        col = day + 2
        current = date(2026, 10, day)
        sheet.cell(1, col, day)
        sheet.cell(2, col, current.strftime("%a"))
        sheet.cell(3, col, "WO" if current.weekday() >= 5 else "G")
    sheet["A3"] = "Demo Agent"
    sheet["B3"] = "Imaging"
    sheet["A4"] = "Leave Agent"
    sheet["B4"] = "Imaging"
    for day in range(1, 32):
        current = date(2026, 10, day)
        sheet.cell(4, day + 2, "WO" if current.weekday() >= 5 else "G")
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def make_consolidated_workbook():
    book = Workbook()
    sheet = book.active
    sheet.title = "Roster Data"
    sheet["A1"] = "Oct'26"
    sheet["A2"] = "Location"
    sheet["B2"] = "Employee Name"
    sheet["C2"] = "Activity"
    employees = ((3, "HDC3", "HDC3 Agent", "Operations"), (4, "HDC4", "HDC4 Agent", "Operations"))
    for day in range(1, 32):
        column = day + 3
        current = date(2026, 10, day)
        sheet.cell(1, column, day)
        sheet.cell(2, column, current.strftime("%a"))
        for row_number, location, name, activity in employees:
            sheet.cell(row_number, 1, location)
            sheet.cell(row_number, 2, name)
            sheet.cell(row_number, 3, activity)
            sheet.cell(row_number, column, "WO" if current.weekday() >= 5 else "G")
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def make_combined_location_team_workbook():
    book = Workbook()
    sheet = book.active
    sheet.title = "Roster"
    sheet["A1"] = "Oct'26"
    sheet["A2"] = "EMPLOYEE NAME"
    for day in range(1, 32):
        current = date(2026, 10, day)
        sheet.cell(1, day + 2, day)
        sheet.cell(2, day + 2, current.strftime("%a"))
    staff = [
        (f"HDC3 Agent {index}", "HDC3- CS") for index in range(1, 9)
    ] + [("HDC3 Asset", "HDC3- Asset"), ("HDC5 Agent 1", "HDC5- CS"), ("HDC5 Agent 2", "HDC5- CS")]
    for row_number, (name, team) in enumerate(staff, start=3):
        sheet.cell(row_number, 1, name)
        sheet.cell(row_number, 2, team)
        for day in range(1, 32):
            current = date(2026, 10, day)
            if current.month == 10 and current.day == 2:
                code = "PH"
            elif current.weekday() >= 5:
                code = "WO" if "Asset" in team else ("3" if day % 4 == 3 else "2" if day % 4 == 0 else "WO")
            elif "HDC3" in team and name.endswith("1"):
                code = "3"
            elif "HDC3" in team and name.endswith("2"):
                code = "2"
            else:
                code = "G"
            sheet.cell(row_number, day + 2, code)
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def make_mixed_month_workbook():
    book = Workbook()
    hdc3 = book.active
    hdc3.title = "HDC3"
    hdc3["A1"] = "Oct'26"
    hdc3["A2"] = "EMPLOYEE NAME"
    hdc3["B2"] = "Activity"
    for day in range(1, 32):
        current = date(2026, 10, day)
        hdc3.cell(1, day + 2, day)
        hdc3.cell(2, day + 2, current.strftime("%a"))
        hdc3.cell(3, day + 2, "WO" if current.weekday() >= 5 else "G")
    hdc3["A3"] = "HDC3 Agent"
    hdc3["B3"] = "CS"

    hdc5 = book.create_sheet("HDC5")
    hdc5["A1"] = "Sep'26"  # The tab's 31-day weekday grid proves it is October.
    hdc5["A2"] = "EMPLOYEE NAME"
    hdc5["B2"] = "Facility"
    for day in range(1, 32):
        current = date(2026, 10, day)
        hdc5.cell(1, day + 2, day)
        hdc5.cell(2, day + 2, current.strftime("%a"))
        hdc5.cell(3, day + 2, "WO" if current.weekday() >= 5 else "G")
    hdc5["A3"] = "HDC5 Agent"
    hdc5["B3"] = "HDC5"

    supplemental = book.create_sheet("DL & SL")
    supplemental["A1"] = "Oct'26"
    supplemental["A2"] = "EMPLOYEE NAME"
    supplemental["B2"] = "Facility"
    for day in range(1, 32):
        current = date(2026, 10, day)
        supplemental.cell(1, day + 2, day)
        supplemental.cell(2, day + 2, current.strftime("%a"))
        supplemental.cell(3, day + 2, "WO" if current.weekday() >= 5 else "G")
    supplemental["A3"] = "HDC3 supplemental agent"
    supplemental["B3"] = "HDC3"

    dd = book.create_sheet("DD")
    dd["A1"] = "Sep'26"
    dd["A2"] = "EMPLOYEE NAME"
    dd["B2"] = "Activity"
    for day in range(1, 31):
        current = date(2026, 9, day)
        dd.cell(1, day + 2, day)
        dd.cell(2, day + 2, current.strftime("%a"))
        dd.cell(3, day + 2, "WO" if current.weekday() >= 5 else "G")
    dd["A3"] = "DD Agent"
    dd["B3"] = "META"
    output = BytesIO()
    book.save(output)
    return output.getvalue()


class WorkbookWorkflowTests(TestCase):
    def test_mixed_source_months_generate_separate_next_month_rosters(self):
        storage_settings = {
            "default": {"BACKEND": "planner.tests.storage.MemoryStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        }
        with override_settings(STORAGES=storage_settings):
            user = get_user_model().objects.create_user(username="mixed-months", password="Roster-Preview-482!")
            self.client.force_login(user)
            response = self.client.post("/workbooks/upload/", {
                "workbook": SimpleUploadedFile("mixed.xlsx", make_mixed_month_workbook(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            })
            self.assertEqual(response.status_code, 302)
            source = WorkbookImport.objects.get()
            runs = list(GenerationRun.objects.filter(source=source).order_by("target_month"))
            self.assertEqual([run.target_month for run in runs], [date(2026, 10, 1), date(2026, 11, 1)])
            self.assertEqual(set(runs[0].categories), {"DD"})
            self.assertEqual(set(runs[1].categories), {"HDC3", "HDC5"})
            self.assertEqual(sum(row["sheet"] == "HDC3" for row in runs[1].assignments), 2)
            self.assertTrue(any(item["code"] == "source_month_inferred" for item in runs[1].warnings))
            detail = self.client.get(reverse("workbook_detail", args=[source.pk]))
            self.assertContains(detail, "October 2026")
            self.assertContains(detail, "November 2026")

    def test_invalid_upload_shows_inline_error_and_keeps_dashboard(self):
        user = get_user_model().objects.create_user(username="invalid-upload", password="Roster-Preview-482!")
        self.client.force_login(user)
        response = self.client.post("/workbooks/upload/", {
            "workbook": SimpleUploadedFile("invalid.csv", b"not an Excel workbook", content_type="text/csv"),
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Choose an .xlsx or .xlsm workbook.", status_code=400)
        self.assertEqual(WorkbookImport.objects.count(), 0)
        response = self.client.post("/workbooks/upload/", {
            "workbook": SimpleUploadedFile("broken.xlsx", b"not an Excel package", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Excel could not read this workbook", status_code=400)
        self.assertEqual(WorkbookImport.objects.count(), 0)

    def test_consolidated_upload_generates_every_location_automatically(self):
        storage_settings = {
            "default": {"BACKEND": "planner.tests.storage.MemoryStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        }
        with override_settings(STORAGES=storage_settings):
            user = get_user_model().objects.create_user(username="consolidated", password="Roster-Preview-482!")
            self.client.force_login(user)
            response = self.client.post("/workbooks/upload/", {
                "workbook": SimpleUploadedFile("all_locations.xlsx", make_consolidated_workbook(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            })
            self.assertEqual(response.status_code, 302)
            source = WorkbookImport.objects.get()
            self.assertEqual(set(source.sheets.filter(layout_kind="roster").values_list("name", flat=True)), {"HDC3", "HDC4"})
            run = GenerationRun.objects.get(source=source)
            self.assertEqual(set(run.categories), {"HDC3", "HDC4"})
            self.assertEqual(run.target_month, date(2026, 11, 1))
            page = self.client.get(f"/workbooks/{source.pk}/")
            self.assertContains(page, "Previous-month schedule")
            self.assertNotContains(page, 'id="id_location"')
            with run.output_file.open("rb") as generated:
                output = load_workbook(generated, read_only=True, data_only=True)
                self.assertIn("HDC3", output.sheetnames)
                self.assertIn("HDC4", output.sheetnames)
                self.assertEqual(output["HDC3"]["A1"].value, "HDC3 roster · November 2026")
                output.close()

    def test_example_combined_facility_team_sheet_uploads_and_generates_all_locations(self):
        storage_settings = {
            "default": {"BACKEND": "planner.tests.storage.MemoryStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        }
        with override_settings(STORAGES=storage_settings):
            user = get_user_model().objects.create_user(username="example-roster", password="Roster-Preview-482!")
            self.client.force_login(user)
            response = self.client.post("/workbooks/upload/", {
                "workbook": SimpleUploadedFile("october_example.xlsx", make_combined_location_team_workbook(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            })
            self.assertEqual(response.status_code, 302)
            source = WorkbookImport.objects.get()
            self.assertEqual(set(source.sheets.filter(layout_kind="roster").values_list("name", flat=True)), {"HDC3", "HDC5"})
            run = GenerationRun.objects.get(source=source)
            self.assertEqual(run.target_month, date(2026, 11, 1))
            self.assertEqual(set(run.categories), {"HDC3", "HDC5"})
            self.assertTrue(any(item["code"] == "public_holiday_calendar_unavailable" for item in run.warnings))
            result_page = self.client.get(reverse("run_detail", args=[run.pk]))
            self.assertContains(result_page, "Generated next-month roster")
            self.assertContains(result_page, "Uploaded previous-month roster")
            asset_row = next(row for row in run.assignments if row["employee"] == "HDC3 Asset")
            self.assertTrue(all(code == "WO" for index, code in enumerate(asset_row["assignments"], start=1) if date(2026, 11, index).weekday() >= 5))

    def test_upload_applies_exact_leave_notes_and_saved_holidays_automatically(self):
        storage_settings = {
            "default": {"BACKEND": "planner.tests.storage.MemoryStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        }
        with override_settings(STORAGES=storage_settings):
            user = get_user_model().objects.create_user(username="leave-auto", password="Roster-Preview-482!")
            self.client.force_login(user)
            book = load_workbook(BytesIO(make_reference_workbook()))
            leave_sheet = book.create_sheet("Leave Details")
            leave_sheet["F1"] = "Leave Agent"
            leave_sheet["G1"] = "2026-11-05 to 2026-11-06"
            source_file = BytesIO()
            book.save(source_file)
            PublicHoliday.objects.create(day=date(2026, 11, 4), label="Saved calendar", confirmed=True)
            response = self.client.post("/workbooks/upload/", {
                "workbook": SimpleUploadedFile("with_leave.xlsx", source_file.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            })
            self.assertEqual(response.status_code, 302)
            imported = WorkbookImport.objects.get()
            run = GenerationRun.objects.get(source=imported)
            leave_row = next(row for row in run.assignments if row["employee"] == "Leave Agent")
            self.assertTrue(all(code not in {"G", "1", "2", "3"} for code in leave_row["assignments"][3:8]))
            self.assertFalse(any(item["code"] == "leave_data_unavailable" for item in run.warnings))
            self.assertFalse(any(item["code"] == "public_holiday_calendar_unavailable" for item in run.warnings))

    def test_upload_project_and_download_a_separate_workbook(self):
        storage_settings = {
            "default": {"BACKEND": "planner.tests.storage.MemoryStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        }
        with override_settings(STORAGES=storage_settings):
            user = get_user_model().objects.create_user(username="planner", password="Roster-Preview-482!")
            self.client.force_login(user)
            source_bytes = make_reference_workbook()
            response = self.client.post("/workbooks/upload/", {
                "workbook": SimpleUploadedFile("sample.xlsx", source_bytes, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            })
            self.assertEqual(response.status_code, 302)
            source = WorkbookImport.objects.get()
            self.assertEqual(source.sheets.get().name, "HDC3")
            self.assertTrue(source.source_file.storage.exists(source.source_file.name))
            employee = Employee.objects.get(display_name="Demo Agent")
            self.assertEqual(employee.owner, user)
            generation_page = self.client.get(f"/workbooks/{source.pk}/")
            self.assertContains(generation_page, "Next-month roster generated automatically")
            self.assertContains(generation_page, "Previous-month schedule")
            self.assertNotContains(generation_page, 'id="id_location"')

            response = self.client.get(f"/workbooks/{source.pk}/employees/{employee.pk}/edit/")
            self.assertEqual(response.status_code, 200)
            response = self.client.post(f"/workbooks/{source.pk}/employees/{employee.pk}/edit/", {
                "display_name": "Alex Doe",
                "email": "alex@example.com",
                "phone": "555-0123",
                "notes": "Updated before roster generation.",
                "active": "on",
            })
            self.assertEqual(response.status_code, 302)
            employee.refresh_from_db()
            self.assertEqual(employee.display_name, "Alex Doe")
            self.assertTrue(employee.active)

            leave_employee = Employee.objects.get(display_name="Leave Agent")
            response = self.client.post(f"/workbooks/{source.pk}/employees/{leave_employee.pk}/edit/", {
                "display_name": "Leave Agent",
                "email": "",
                "phone": "",
                "notes": "",
                "active": "on",
                "planned_leave": "2026-11-05 to 2026-11-06",
            })
            self.assertEqual(response.status_code, 302)
            leave_employee.refresh_from_db()
            self.assertEqual(leave_employee.planned_leave, [{"start": "2026-11-05", "end": "2026-11-06"}])

            response = self.client.post(f"/workbooks/{source.pk}/employees/{employee.pk}/roster/", {"action": "remove"})
            self.assertEqual(response.status_code, 302)
            self.assertTrue(EmployeeAssignment.objects.get(employee=employee).excluded_from_roster)
            self.assertContains(self.client.get(f"/workbooks/{source.pk}/"), "Previous-month schedule")

            PublicHoliday.objects.create(day=date(2026, 11, 4), label="Saved calendar", source=source, confirmed=True)
            response = self.client.post("/rosters/generate/", {
                "source_id": str(source.pk),
                "target_month": "11",
                "target_year": "2026",
                "location": "HDC3",
            })
            self.assertEqual(response.status_code, 302)
            run = GenerationRun.objects.filter(source=source).order_by("-created_at").first()
            self.assertEqual(run.target_month, date(2026, 11, 1))
            self.assertEqual(run.status, "review")
            self.assertEqual(run.assignments[0]["employee"], "Alex Doe")
            self.assertTrue(run.assignments[0]["active"])
            self.assertFalse(run.assignments[0]["included"])
            self.assertTrue(all(not code for code in run.assignments[0]["assignments"]))
            # Leave on Nov 5-6 blocks the adjacent Nov 4 holiday and Nov 7-8 weekend.
            self.assertFalse(any(code in {"G", "1", "2", "3"} for code in run.assignments[1]["assignments"][3:8]))
            self.assertFalse(any(item["code"] == "holiday_intake_out_of_scope" for item in run.warnings))
            self.assertTrue(run.output_file.storage.exists(run.output_file.name))

            response = self.client.get(f"/rosters/{run.pk}/download/")
            self.assertEqual(response.status_code, 302)
            self.assertTrue(any(item.get("severity") == "error" for item in run.warnings))

            response = self.client.post(f"/workbooks/{source.pk}/employees/{employee.pk}/roster/", {"action": "restore"})
            self.assertEqual(response.status_code, 302)
            self.assertFalse(EmployeeAssignment.objects.get(employee=employee).excluded_from_roster)
            with source.source_file.open("rb") as original:
                source_book = load_workbook(original, read_only=True, data_only=False)
                self.assertEqual(source_book["HDC3"]["A1"].value, "Oct'26")
                source_book.close()

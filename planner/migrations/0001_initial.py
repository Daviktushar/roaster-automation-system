# Generated from planner/models.py for the first database setup.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="Activity",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("name", models.CharField(max_length=120, unique=True)), ("description", models.TextField(blank=True))],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="Employee",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("display_name", models.CharField(max_length=180)), ("normalized_name", models.CharField(max_length=180, unique=True)), ("active", models.BooleanField(default=True))],
            options={"ordering": ["display_name"]},
        ),
        migrations.CreateModel(
            name="ShiftCode",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("code", models.CharField(max_length=16, unique=True)), ("category", models.CharField(default="unknown", max_length=16)), ("description", models.CharField(blank=True, max_length=140)), ("needs_review", models.BooleanField(default=True))],
            options={"ordering": ["code"]},
        ),
        migrations.CreateModel(
            name="WorkbookImport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_file", models.FileField(upload_to="workbooks/%Y/%m/")),
                ("source_filename", models.CharField(max_length=255)),
                ("uploaded_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("status", models.CharField(choices=[("ready", "Ready"), ("warning", "Imported with warnings"), ("failed", "Failed")], default="ready", max_length=12)),
                ("sheet_summary", models.JSONField(blank=True, default=list)),
                ("warnings", models.JSONField(blank=True, default=list)),
                ("uploaded_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="workbook_imports", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="LeaveNote",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("employee_name", models.CharField(max_length=180)), ("raw_date_text", models.TextField(blank=True)), ("raw_note", models.TextField(blank=True)),
                ("normalized_start", models.DateField(blank=True, null=True)), ("normalized_end", models.DateField(blank=True, null=True)), ("needs_review", models.BooleanField(default=True)),
                ("workbook", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="leave_notes", to="planner.workbookimport")),
            ],
        ),
        migrations.CreateModel(
            name="PublicHoliday",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("day", models.DateField(unique=True)), ("label", models.CharField(blank=True, max_length=140)), ("confirmed", models.BooleanField(default=False)),
                ("source", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="planner.workbookimport")),
            ],
            options={"ordering": ["day"]},
        ),
        migrations.CreateModel(
            name="RosterSheet",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("name", models.CharField(max_length=100)), ("hidden", models.BooleanField(default=False)), ("roster_month", models.DateField(blank=True, null=True)), ("layout_kind", models.CharField(default="reference", max_length=30)), ("source_headers", models.JSONField(blank=True, default=list)), ("row_count", models.PositiveIntegerField(default=0)), ("data_rows", models.JSONField(blank=True, default=list)),
                ("workbook", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sheets", to="planner.workbookimport")),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.CreateModel(
            name="GenerationRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("target_month", models.DateField(db_index=True)), ("categories", models.JSONField(blank=True, default=list)), ("status", models.CharField(choices=[("preview", "Preview ready"), ("review", "Needs review"), ("approved", "Approved")], default="review", max_length=12)), ("output_file", models.FileField(blank=True, upload_to="generated/%Y/%m/")), ("warnings", models.JSONField(blank=True, default=list)), ("assignments", models.JSONField(blank=True, default=list)), ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="generation_runs", to=settings.AUTH_USER_MODEL)), ("source", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="generation_runs", to="planner.workbookimport")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="EmployeeAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("category_label", models.CharField(blank=True, max_length=140)), ("row_order", models.PositiveIntegerField(default=0)),
                ("activity", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="planner.activity")), ("employee", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="category_assignments", to="planner.employee")), ("roster_sheet", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="employee_assignments", to="planner.rostersheet")),
            ],
            options={"ordering": ["row_order"]},
        ),
        migrations.AddConstraint(model_name="rostersheet", constraint=models.UniqueConstraint(fields=("workbook", "name"), name="unique_sheet_per_import")),
        migrations.AddConstraint(model_name="employeeassignment", constraint=models.UniqueConstraint(fields=("employee", "roster_sheet"), name="unique_employee_per_sheet")),
    ]

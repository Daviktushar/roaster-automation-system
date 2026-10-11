from django.conf import settings
from django.db import models


class WorkbookImport(models.Model):
    STATUS_CHOICES = [("ready", "Ready"), ("warning", "Imported with warnings"), ("failed", "Failed")]
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="workbook_imports")
    source_file = models.FileField(upload_to="workbooks/%Y/%m/")
    source_filename = models.CharField(max_length=255)
    uploaded_at = models.DateTimeField(auto_now_add=True, db_index=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="ready")
    sheet_summary = models.JSONField(default=list, blank=True)
    warnings = models.JSONField(default=list, blank=True)

    def __str__(self):
        return f"{self.source_filename} ({self.uploaded_at:%Y-%m-%d})"


class RosterSheet(models.Model):
    workbook = models.ForeignKey(WorkbookImport, on_delete=models.CASCADE, related_name="sheets")
    name = models.CharField(max_length=100)
    hidden = models.BooleanField(default=False)
    roster_month = models.DateField(null=True, blank=True)
    layout_kind = models.CharField(max_length=30, default="reference")
    source_headers = models.JSONField(default=list, blank=True)
    row_count = models.PositiveIntegerField(default=0)
    data_rows = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workbook", "name"], name="unique_sheet_per_import")]
        ordering = ["id"]

    def __str__(self):
        return f"{self.name} · {self.workbook.source_filename}"


class GenerationRun(models.Model):
    STATUS_CHOICES = [("preview", "Preview ready"), ("review", "Needs review"), ("approved", "Approved")]
    source = models.ForeignKey(WorkbookImport, on_delete=models.PROTECT, related_name="generation_runs")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="generation_runs")
    target_month = models.DateField(db_index=True)
    categories = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="review")
    output_file = models.FileField(upload_to="generated/%Y/%m/", blank=True)
    warnings = models.JSONField(default=list, blank=True)
    assignments = models.JSONField(default=list, blank=True)
    validation_report = models.JSONField(default=dict, blank=True)
    validation_status = models.CharField(max_length=20, default="NEEDS_ATTENTION")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]


class Employee(models.Model):
    """Names are taken from roster rows; no employee ID exists in the source file."""
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="roster_employees")
    display_name = models.CharField(max_length=180)
    normalized_name = models.CharField(max_length=180)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    employee_code = models.CharField(max_length=80, blank=True)
    transport_preference = models.CharField(max_length=40, blank=True)
    notes = models.TextField(blank=True)
    planned_leave = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_name"]
        constraints = [models.UniqueConstraint(fields=["owner", "normalized_name"], name="unique_employee_name_per_owner")]

    def __str__(self):
        return self.display_name


class Activity(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ShiftCode(models.Model):
    """Observed cell codes are stored without assuming their business meaning."""
    code = models.CharField(max_length=16, unique=True)
    category = models.CharField(max_length=16, default="unknown")
    description = models.CharField(max_length=140, blank=True)
    needs_review = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class EmployeeAssignment(models.Model):
    """Normalized identity/category reference; assignment details stay in the source sheet snapshot."""
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="category_assignments")
    roster_sheet = models.ForeignKey(RosterSheet, on_delete=models.CASCADE, related_name="employee_assignments")
    activity = models.ForeignKey(Activity, null=True, blank=True, on_delete=models.SET_NULL)
    category_label = models.CharField(max_length=140, blank=True)
    row_order = models.PositiveIntegerField(default=0)
    excluded_from_roster = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["employee", "roster_sheet"], name="unique_employee_per_sheet")]
        ordering = ["row_order"]


class LeaveNote(models.Model):
    """Raw leave text is retained until it can be safely normalized."""
    workbook = models.ForeignKey(WorkbookImport, on_delete=models.CASCADE, related_name="leave_notes")
    employee_name = models.CharField(max_length=180)
    raw_date_text = models.TextField(blank=True)
    raw_note = models.TextField(blank=True)
    normalized_start = models.DateField(null=True, blank=True)
    normalized_end = models.DateField(null=True, blank=True)
    needs_review = models.BooleanField(default=True)


class PublicHoliday(models.Model):
    day = models.DateField(unique=True)
    label = models.CharField(max_length=140, blank=True)
    source = models.ForeignKey(WorkbookImport, null=True, blank=True, on_delete=models.SET_NULL)
    confirmed = models.BooleanField(default=False)

    class Meta:
        ordering = ["day"]


class StaffingRequirement(models.Model):
    DAY_TYPES = [("weekday", "Weekdays"), ("weekend", "Weekends"), ("date", "Specific date")]
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="staffing_requirements")
    facility = models.CharField(max_length=40)
    shift_code = models.CharField(max_length=16)
    day_type = models.CharField(max_length=12, choices=DAY_TYPES)
    specific_date = models.DateField(null=True, blank=True)
    required_count = models.PositiveIntegerField()
    effective_from = models.DateField(help_text="First day of the month when this target applies")
    effective_to = models.DateField(null=True, blank=True)
    source_workbook = models.ForeignKey(WorkbookImport, null=True, blank=True, on_delete=models.SET_NULL, related_name="staffing_targets")
    confirmation_note = models.CharField(max_length=500, blank=True)
    confirmed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["facility", "shift_code", "day_type", "effective_from"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "facility", "shift_code", "effective_from", "day_type"], condition=models.Q(specific_date__isnull=True), name="unique_staffing_requirement_period"),
            models.UniqueConstraint(fields=["owner", "facility", "shift_code", "specific_date"], condition=models.Q(specific_date__isnull=False), name="unique_staffing_requirement_date"),
        ]


class HistoricalRosterSnapshot(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="roster_history")
    workbook = models.ForeignKey(WorkbookImport, on_delete=models.CASCADE, related_name="history_snapshots")
    roster_sheet = models.ForeignKey(RosterSheet, on_delete=models.CASCADE, related_name="history_snapshots")
    employee = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="roster_history")
    employee_name = models.CharField(max_length=180)
    employee_key = models.CharField(max_length=180)
    roster_month = models.DateField()
    assignments = models.JSONField(default=dict, blank=True)
    pattern = models.CharField(max_length=16, blank=True)
    transport_preference = models.CharField(max_length=40, blank=True)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["roster_month", "employee_name"]
        constraints = [models.UniqueConstraint(fields=["roster_sheet", "employee_key"], name="unique_history_row_per_employee_sheet")]


class PatternHistoryBaseline(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pattern_baselines")
    effective_from = models.DateField()
    note = models.TextField()
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="confirmed_pattern_baselines")
    confirmed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_from"]
        constraints = [models.UniqueConstraint(fields=["owner"], name="unique_pattern_baseline_per_owner")]


class EmployeePatternChange(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="pattern_changes")
    effective_start = models.DateField()
    effective_end = models.DateField(null=True, blank=True)
    previous_pattern = models.CharField(max_length=16, blank=True)
    new_pattern = models.CharField(max_length=16)
    changed_on = models.DateField()
    source = models.CharField(max_length=40)
    source_snapshot = models.ForeignKey(HistoricalRosterSnapshot, null=True, blank=True, on_delete=models.SET_NULL, related_name="pattern_changes")
    generation_run = models.ForeignKey(GenerationRun, null=True, blank=True, on_delete=models.SET_NULL, related_name="pattern_changes")
    verified = models.BooleanField(default=True)

    class Meta:
        ordering = ["effective_start", "employee_id"]
        constraints = [models.UniqueConstraint(fields=["employee", "effective_start", "new_pattern"], name="unique_employee_pattern_start")]

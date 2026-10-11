from django.contrib import admin

from planner.models import Activity, Employee, EmployeeAssignment, EmployeePatternChange, GenerationRun, HistoricalRosterSnapshot, LeaveNote, PatternHistoryBaseline, PublicHoliday, RosterSheet, ShiftCode, StaffingRequirement, WorkbookImport


@admin.register(WorkbookImport)
class WorkbookImportAdmin(admin.ModelAdmin):
    list_display = ("source_filename", "uploaded_by", "uploaded_at", "status")
    list_filter = ("status", "uploaded_at")
    search_fields = ("source_filename", "uploaded_by__username")


@admin.register(GenerationRun)
class GenerationRunAdmin(admin.ModelAdmin):
    list_display = ("target_month", "created_by", "status", "created_at")
    list_filter = ("status", "target_month")


admin.site.register((RosterSheet, Employee, Activity, ShiftCode, EmployeeAssignment, LeaveNote, PublicHoliday, StaffingRequirement, HistoricalRosterSnapshot, PatternHistoryBaseline, EmployeePatternChange))

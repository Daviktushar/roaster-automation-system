from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone
from datetime import date
import calendar
import json

from planner.models import Employee, StaffingRequirement, WorkbookImport


class SignUpForm(UserCreationForm):
    email = forms.EmailField()
    first_name = forms.CharField(max_length=150)

    class Meta:
        model = User
        fields = ("first_name", "email", "username", "password1", "password2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({"class": "form-control auth-control"})
        self.fields["password1"].help_text = "Use at least 8 characters. Django checks common and weak passwords."
        self.fields["password2"].help_text = "Enter the same password again for confirmation."

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email


class WorkbookUploadForm(forms.Form):
    workbook = forms.FileField()

    def clean_workbook(self):
        file = self.cleaned_data["workbook"]
        if not file.name.lower().endswith((".xlsx", ".xlsm")):
            raise forms.ValidationError("Choose an .xlsx or .xlsm workbook.")
        if file.size > 20 * 1024 * 1024:
            raise forms.ValidationError("Workbook must be 20 MB or smaller.")
        return file


class GenerationForm(forms.Form):
    source_id = forms.IntegerField(widget=forms.HiddenInput())
    target_month = forms.ChoiceField(label="Roster month")
    target_year = forms.ChoiceField(label="Roster year")
    public_holidays = forms.CharField(
        required=False,
        label="Target-month public holidays",
        help_text="Optional. Enter confirmed dates as YYYY-MM-DD, one per line.",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "2026-11-14"}),
    )
    location = forms.ChoiceField(label="Location", required=True)

    def __init__(self, *args, workbook_import=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.workbook_import = workbook_import
        choices = []
        location_periods = {}
        if workbook_import:
            for sheet in workbook_import.sheets.filter(layout_kind="roster"):
                label = "DD (tab name has trailing space)" if sheet.name == "DD " else sheet.name
                choices.append((sheet.name, label))
        self.fields["location"].choices = choices
        for sheet in workbook_import.sheets.filter(layout_kind="roster") if workbook_import else []:
            if not sheet.roster_month:
                continue
            next_period = date(sheet.roster_month.year + (sheet.roster_month.month == 12), 1 if sheet.roster_month.month == 12 else sheet.roster_month.month + 1, 1)
            location_periods[sheet.name] = {"month": str(next_period.month), "year": str(next_period.year), "label": next_period.strftime("%B %Y")}
        self.location_periods_json = json.dumps(location_periods)
        if choices:
            self.fields["location"].initial = choices[0][0]
        self.fields["source_id"].initial = workbook_import.pk if workbook_import else None
        today = timezone.localdate()
        source_sheets = workbook_import.sheets.filter(layout_kind="roster").exclude(roster_month__isnull=True) if workbook_import else []
        selected_location = (kwargs.get("data", {}).get("location") if kwargs.get("data") else None) or (choices[0][0] if choices else None)
        if selected_location:
            source_sheets = source_sheets.filter(name=selected_location)
        source_months = list(source_sheets.values_list("roster_month", flat=True))
        source_months = {date(item.year, item.month, 1) for item in source_months}
        if len(source_months) == 1:
            source_month = next(iter(source_months))
            next_month = date(source_month.year + (1 if source_month.month == 12 else 0), 1 if source_month.month == 12 else source_month.month + 1, 1)
        else:
            next_month = date(today.year + (1 if today.month == 12 else 0), 1 if today.month == 12 else today.month + 1, 1)
        self.next_month_value = {"month": str(next_month.month), "year": str(next_month.year)}
        self.next_month_label = next_month.strftime("%B %Y")
        self.fields["target_month"].choices = [(str(month), calendar.month_name[month]) for month in range(1, 13)]
        self.fields["target_year"].choices = [(str(year), str(year)) for year in range(today.year - 1, today.year + 11)]
        self.fields["target_month"].initial = str(next_month.month)
        self.fields["target_year"].initial = str(next_month.year)
        self.fields["target_month"].widget.attrs.update({"class": "form-select"})
        self.fields["target_year"].widget.attrs.update({"class": "form-select"})
        self.fields["location"].widget.attrs.update({"class": "form-select"})
        self.fields["location"].widget.attrs["data-periods"] = self.location_periods_json

    def clean_target_month(self):
        return int(self.cleaned_data["target_month"])

    def clean_target_year(self):
        return int(self.cleaned_data["target_year"])

    def clean_public_holidays(self):
        target_month = self.cleaned_data.get("target_month")
        target_year = self.cleaned_data.get("target_year")
        holidays = set()
        for line in self.cleaned_data["public_holidays"].splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                day = date.fromisoformat(line)
            except ValueError as exc:
                raise forms.ValidationError("Enter public holiday dates in YYYY-MM-DD format, one per line.") from exc
            if target_month and target_year and (day.year, day.month) != (target_year, target_month):
                raise forms.ValidationError("Each public holiday date must be in the selected target month.")
            holidays.add(day)
        return holidays

    def clean_source_id(self):
        source_id = self.cleaned_data["source_id"]
        if not self.workbook_import or source_id != self.workbook_import.pk:
            raise forms.ValidationError("Select one of your imported workbooks.")
        return source_id

    def clean(self):
        cleaned = super().clean()
        if not self.workbook_import or not cleaned.get("target_month") or not cleaned.get("target_year"):
            return cleaned
        selected = cleaned.get("location")
        sheets = self.workbook_import.sheets.filter(layout_kind="roster")
        if selected:
            sheets = sheets.filter(name=selected)
        source_months = {date(item.year, item.month, 1) for item in sheets.exclude(roster_month__isnull=True).values_list("roster_month", flat=True)}
        if not source_months:
            self.add_error("location", "The selected location has no detected source month. Check the uploaded roster headers.")
        elif len(source_months) > 1:
            self.add_error("location", "Select a location with a detected source month.")
        elif len(source_months) == 1:
            source = next(iter(source_months))
            expected = date(source.year + (source.month == 12), 1 if source.month == 12 else source.month + 1, 1)
            selected_target = date(cleaned["target_year"], cleaned["target_month"], 1)
            if selected_target != expected:
                self.add_error("target_month", f"This source roster is {source:%B %Y}; the next roster must be {expected:%B %Y}.")
        return cleaned


class EmployeeDetailsForm(forms.ModelForm):
    planned_leave = forms.CharField(
        required=False,
        label="Planned leave dates",
        help_text="Enter confirmed dates as YYYY-MM-DD, one per line. Use YYYY-MM-DD to YYYY-MM-DD for a range.",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 4, "placeholder": "2026-11-12 to 2026-11-14"}),
    )

    class Meta:
        model = Employee
        fields = ("display_name", "employee_code", "transport_preference", "email", "phone", "active", "notes")
        widgets = {
            "display_name": forms.TextInput(attrs={"class": "form-control"}),
            "employee_code": forms.TextInput(attrs={"class": "form-control"}),
            "transport_preference": forms.TextInput(attrs={"class": "form-control", "placeholder": "OWN or CAB"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "phone": forms.TextInput(attrs={"class": "form-control", "type": "tel"}),
            "active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and not self.is_bound:
            self.fields["planned_leave"].initial = "\n".join(
                f"{item['start']} to {item['end']}" if item["start"] != item["end"] else item["start"]
                for item in self.instance.planned_leave
            )

    def clean_display_name(self):
        display_name = " ".join(self.cleaned_data["display_name"].split())
        normalized = display_name.casefold()
        duplicates = Employee.objects.filter(owner=self.instance.owner, normalized_name=normalized)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise forms.ValidationError("An employee with this name already exists in your workspace.")
        self.cleaned_data["normalized_name"] = normalized
        return display_name

    def clean_planned_leave(self):
        ranges = []
        for line in self.cleaned_data["planned_leave"].splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [part.strip() for part in line.split(" to ", 1)]
            try:
                start = date.fromisoformat(parts[0])
                end = date.fromisoformat(parts[1]) if len(parts) == 2 else start
            except ValueError as exc:
                raise forms.ValidationError("Use YYYY-MM-DD or YYYY-MM-DD to YYYY-MM-DD for each leave entry.") from exc
            if end < start:
                raise forms.ValidationError("A leave end date cannot be earlier than its start date.")
            ranges.append({"start": start.isoformat(), "end": end.isoformat()})
        return ranges

    def save(self, commit=True):
        employee = super().save(commit=False)
        employee.normalized_name = self.cleaned_data["normalized_name"]
        employee.planned_leave = self.cleaned_data["planned_leave"]
        if commit:
            employee.save()
        return employee


class StaffingConfigurationForm(forms.Form):
    effective_month = forms.DateField(widget=forms.DateInput(attrs={"type": "month", "class": "form-control"}, format="%Y-%m"), input_formats=["%Y-%m"])
    requirements = forms.CharField(
        label="Confirmed staffing targets",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 10, "spellcheck": "false"}),
        help_text="One target per line: facility, shift, weekday|weekend|everyday|YYYY-MM-DD, headcount. Date targets override the weekday/weekend target for that date. Example: HDC3,2,2026-11-02,4",
    )
    confirmation_note = forms.CharField(label="Confirmation source / note", max_length=500, widget=forms.TextInput(attrs={"class": "form-control"}))

    def __init__(self, *args, owner=None, workbook_import=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner
        self.workbook_import = workbook_import
        self.fields["effective_month"].initial = timezone.localdate().replace(day=1).strftime("%Y-%m")
        config_month = timezone.localdate().replace(day=1)
        current = StaffingRequirement.objects.filter(owner=owner, effective_to__isnull=True).filter(Q(specific_date__isnull=True) | Q(specific_date__gte=config_month)).order_by("facility", "shift_code", "day_type") if owner else []
        if not self.is_bound and current:
            self.fields["requirements"].initial = "\n".join(f"{item.facility},{item.shift_code},{item.specific_date.isoformat() if item.specific_date else item.day_type},{item.required_count}" for item in current)
            self.fields["confirmation_note"].initial = "Confirmed staffing targets"
        if workbook_import:
            self.base_file_rows = next((sheet.data_rows for sheet in workbook_import.sheets.filter(layout_kind="staffing_reference")), [])
        else:
            self.base_file_rows = []

    def clean_requirements(self):
        result = []
        valid_shifts = {"G", "1", "2", "3"}
        for line_number, line in enumerate(self.cleaned_data["requirements"].splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            fields = [part.strip() for part in line.split(",")]
            if len(fields) != 4:
                raise forms.ValidationError(f"Line {line_number}: use facility, shift, day type, headcount.")
            facility, shift, day_type, count = fields
            shift = shift.upper()
            day_type = day_type.casefold()
            if not count.isdigit() or int(count) < 1:
                raise forms.ValidationError(f"Line {line_number}: headcount must be a positive integer.")
            if facility not in {"HDC3", "HDC4", "HDC5"} or shift not in valid_shifts:
                raise forms.ValidationError(f"Line {line_number}: select HDC3/HDC4/HDC5 and shift G, 1, 2, or 3.")
            available = {
                ("HDC3", "weekday"): {"G", "2", "3"}, ("HDC3", "weekend"): {"1", "2", "3"},
                ("HDC4", "weekday"): {"G", "1", "2"}, ("HDC4", "weekend"): {"1", "2"},
                ("HDC5", "weekday"): {"G"}, ("HDC5", "weekend"): set(),
            }
            specific_date = None
            if day_type not in {"weekday", "weekend", "everyday"}:
                try:
                    specific_date = date.fromisoformat(day_type)
                except ValueError as exc:
                    raise forms.ValidationError(f"Line {line_number}: use weekday, weekend, everyday, or a YYYY-MM-DD date.") from exc
                if specific_date.strftime("%Y-%m") != self.data.get("effective_month"):
                    raise forms.ValidationError(f"Line {line_number}: specific staffing dates must be in the effective month.")
                day_type = "date"
            if day_type == "everyday":
                if shift not in available[(facility, "weekday")] or shift not in available[(facility, "weekend")]:
                    raise forms.ValidationError(f"Line {line_number}: {shift} is not available every day at {facility}; enter separate weekday/weekend targets.")
                result.extend((facility, shift, kind, int(count), None) for kind in ("weekday", "weekend"))
                continue
            day_kind = "weekend" if specific_date and specific_date.weekday() >= 5 else "weekday" if specific_date else day_type
            if day_type not in {"weekday", "weekend", "date"} or not count.isdigit() or int(count) < 1:
                raise forms.ValidationError(f"Line {line_number}: day type must be weekday, weekend, or everyday and headcount must be a positive integer.")
            if shift not in available[(facility, day_kind)]:
                raise forms.ValidationError(f"Line {line_number}: shift {shift} is not available at {facility} on {day_type}s.")
            result.append((facility, shift, day_type, int(count), specific_date))
        keys = [(item[0], item[1], item[2], item[4]) for item in result]
        if len(keys) != len(set(keys)):
            raise forms.ValidationError("Each facility, shift, and day-type combination can appear once.")
        if not result:
            raise forms.ValidationError("Enter at least one confirmed staffing target.")
        return result

    def save(self):
        month = self.cleaned_data["effective_month"].replace(day=1)
        keys = {(facility, shift, day_type, specific_date) for facility, shift, day_type, _, specific_date in self.cleaned_data["requirements"]}
        active = StaffingRequirement.objects.filter(owner=self.owner, effective_to__isnull=True, effective_from__lte=month)
        for obsolete in active:
            key = (obsolete.facility, obsolete.shift_code, obsolete.day_type, obsolete.specific_date)
            if key not in keys:
                if obsolete.effective_from < month:
                    obsolete.effective_to = month.fromordinal(month.toordinal() - 1)
                    obsolete.save(update_fields=["effective_to"])
                elif obsolete.effective_from == month:
                    obsolete.delete()
        for facility, shift, day_type, count, specific_date in self.cleaned_data["requirements"]:
            prior = StaffingRequirement.objects.filter(owner=self.owner, facility=facility, shift_code=shift, day_type=day_type, specific_date=specific_date, effective_to__isnull=True, effective_from__lt=month)
            prior.update(effective_to=month.fromordinal(month.toordinal() - 1))
            StaffingRequirement.objects.update_or_create(
                owner=self.owner, facility=facility, shift_code=shift, day_type=day_type, specific_date=specific_date, effective_from=month,
                defaults={"required_count": count, "source_workbook": self.workbook_import, "confirmation_note": self.cleaned_data["confirmation_note"]},
            )


class PatternBaselineForm(forms.Form):
    effective_from = forms.DateField(label="Prospective tracking starts", widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}))
    note = forms.CharField(label="Baseline confirmation", widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}), help_text="Record that earlier shift-pattern history is unavailable and explain the basis for starting prospective tracking.")

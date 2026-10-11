# Monthly Roster Automation System

A Windows-friendly roster workspace built in small, reviewable phases. The standalone frontend runs without Django. The Django app supports workbook import, inspection, draft generation, review, and separate Excel export.

## Current behavior

- `frontend/` is a browser-only preview with sample data. It does not save account credentials or upload workbook contents.
- Django signup, login, logout, and password reset use Django authentication. Development password-reset mail is printed to the server terminal.
- Workbook import stores an uploaded copy and keeps each worksheet name exactly, including hidden sheets and trailing whitespace. The original source file is never rewritten.
- One uploaded workbook can contain all locations as separate roster tabs, or as one consolidated roster table with a `Location`/`Facility` column, an `Employee Name` column, optional `Activity`/`Team`, and numbered day columns. Consolidated inputs are split into location-specific tabs in the generated workbook.
- After import, use the employee list before generating: edit display name, employee code, transport preference, email, phone, notes, and active status, or remove an employee from this workbook's future roster drafts. Removed employees can be restored.
- The target month is set to the next month after the source roster when the source month is identifiable. A mismatched period is rejected, and the selected full month and year appears at the top of each exported roster tab.
- Normal monthly generation needs only the uploaded previous roster, location, and target month/year. Saved staffing targets are reused automatically; otherwise, targets are inferred from the location's source assignments and labeled provisional. Missing annual history is reported and does not require manual confirmation to generate a provisional workbook.
- Runs track annual pattern changes and separate rule, staffing, history, feasibility, and overall statuses. A roster with mandatory conflicts or unsafe source structure cannot be downloaded. A warning-only `NEEDS_ATTENTION` roster can be downloaded as provisional and is never labeled `VALID`.
- Generation uses the previous roster, applies HDC3/HDC4/HDC5 coverage rules, keeps Asset teams off weekends, provides rest after shift 3 before day shifts, caps work streaks at five days, excludes previous-month night workers from new night shifts, and prefers own-vehicle staff when eligible.
- Planned leave is entered on each employee profile. Leave dates and adjacent intervening holidays/weekends are blocked for that employee. Public holidays can be supplied for a target month when available; missing history or inferred targets are called out in the generated validation report.
- The exporter patches a copy of the original Excel package so it retains the workbook's styles, formulas, hidden sheets, widths, and merged cells. Excel recalculates formulas when the copy is opened.
- Excel exports include a Validation Summary worksheet and remain separate from the uploaded source. The application does not claim a roster is valid merely because an XLSX was created.

## Run the working application (frontend and backend)

The working frontend is served by Django, which also handles accounts, workbook uploads, roster generation, and downloads. From PowerShell in this folder, start the complete local setup with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_app.ps1
```

The script creates `.venv` if needed, bootstraps pip, installs `requirements.txt`, creates `.env` from `.env.example` if needed, applies migrations, and starts Django. Open <http://127.0.0.1:8000>. Stop the server with Ctrl+C.

To check that the backend is ready, open a second PowerShell window in the project folder and run:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test
```

The first command should report `System check identified no issues`; the second should end with `OK`. To use the app, create an account, upload one previous-month workbook for all locations, select the location and target month/year, then generate and download the next roster. The target period defaults to the month immediately after the detected source month. Review the validation summary: provisional targets or incomplete history are identified explicitly, and unresolved mandatory conflicts block download.

## Run the standalone demo preview (optional)

This is a separate, browser-only sample workspace. It does not connect to Django, save credentials, or upload files. Keep it on port 8001 so it cannot conflict with Django:

```powershell
py -3.14 -m http.server 8001 --directory frontend
```

Open <http://localhost:8001>. External Bootstrap and Tailwind assets load from their CDNs, so framework styling requires browser internet access.

## Manual Django setup

Create a virtual environment and install dependencies:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

The `.env.example` uses SQLite for a first local run. To use the installed MySQL server, run `powershell -ExecutionPolicy Bypass -File .\scripts\create_mysql_database.ps1`; it asks for your MySQL administrator password without echoing it, creates a new database and least-purpose app user, and writes private credentials to `.env`. It does not overwrite an existing `.env` or database.

## Project structure

```text
frontend/                 Browser-only frontend preview
  assets/css/             Shared styles
  assets/js/              Demo interactions and local sample data
config/                   Django settings and URL configuration
planner/                  Django models, forms, views, and services
  services/               Workbook import, rule checks, and XLSX export
templates/planner/        Django templates for auth and roster workflow
scripts/                  Local database setup helper
docs/                     Workbook findings and rule assumptions
```

## Run project checks

After dependencies are installed:

```powershell
python manage.py check
python manage.py test
```

Review a generated workbook in Excel before operational use. Keep `.env`, uploaded workbooks, and generated output out of Git.

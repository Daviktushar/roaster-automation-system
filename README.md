# roaster-automation-system

Web Based Roaster Automation System.

A Windows-friendly roster workspace built in small, reviewable phases. The standalone frontend runs without Django. The Django app supports workbook import, inspection, draft generation, review, and separate Excel export.

## Current behavior

- `frontend/` is a browser-only preview with sample data. It does not save account credentials or upload workbook contents.
- Django signup, login, logout, and password reset use Django authentication. Development password-reset mail is printed to the server terminal.
- Workbook import stores an uploaded copy and keeps each worksheet name exactly, including hidden sheets and trailing whitespace. The original source file is never rewritten.
- One uploaded workbook can contain all locations as separate roster tabs, a consolidated table with `Location`/`Facility` and `Employee Name` columns, or the supplied layout where values such as `HDC3- CS` and `HDC5 Asset` identify location and team. Consolidated inputs are split into location-specific tabs in the generated workbook.
- Employee names, teams, and shifts are taken directly from the workbook. Optional profile details can be saved, but they are not required for monthly generation.
- The next target month is derived from the roster date headers when all detected locations share one source month. The generated workbook displays the full target month and year on each location tab.
- Normal monthly generation needs only one uploaded previous-month workbook. The target period and locations are detected automatically, and a next-month roster is generated for every detected location. Saved staffing targets are reused automatically; otherwise, targets are inferred from matching source assignments and labeled provisional. Missing annual history is reported and does not require manual confirmation to generate a provisional workbook.
- Runs track annual pattern changes and separate rule, staffing, history, feasibility, and overall statuses. A roster with mandatory conflicts or unsafe source structure cannot be downloaded. A warning-only `NEEDS_ATTENTION` roster can be downloaded as provisional and is never labeled `VALID`.
- Generation uses the previous roster, applies HDC3/HDC4/HDC5 coverage rules, keeps Asset teams off weekends, provides rest after shift 3 before day shifts, caps work streaks at five days, excludes previous-month night workers from new night shifts, and prefers own-vehicle staff when eligible.
- Exact future leave dates from a workbook's Leave Details tab and saved employee records are applied automatically. Confirmed holidays already in the project's calendar are reused automatically. When the upload does not contain future leave or holiday details, the output calls out that limitation and remains provisional.
- The exporter patches a copy of the original Excel package so it retains the workbook's styles, formulas, hidden sheets, widths, and merged cells. Excel recalculates formulas when the copy is opened.
- Excel exports include a Validation Summary worksheet and remain separate from the uploaded source. The application does not claim a roster is valid merely because an XLSX was created.

## Run the working application (frontend and backend)

The working frontend is served by Django, which also handles accounts, workbook uploads, roster generation, and downloads. Upload one previous-month workbook to start the flow. If its tabs contain different source months, the system creates one next-month output per source-month group. It checks day counts and weekday headers against month labels; a provable correction is called out in the results and keeps that roster provisional until verified. From PowerShell in this folder, start the complete local setup with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_app.ps1
```

The script creates `.venv` if needed, bootstraps pip, installs `requirements.txt`, creates `.env` from `.env.example` if needed, applies migrations, and starts Django. Open <http://127.0.0.1:8000>. Stop the server with Ctrl+C.

To check that the backend is ready, open a second PowerShell window in the project folder and run:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test
```

The first command should report `System check identified no issues`; the second should end with `OK`. To use the app, create an account and upload one previous-month workbook. The application detects the source month and locations, automatically creates the next-month roster, and displays the uploaded and generated schedules. Download the output from the result page. Provisional targets or incomplete history are identified explicitly, and unresolved mandatory conflicts block download.

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

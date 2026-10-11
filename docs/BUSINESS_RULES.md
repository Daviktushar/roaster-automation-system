# Roster automation rules and verification

## Configured through the application

- Previously saved staffing targets are reused automatically. When an unspecified count is needed, the generator infers a median same-weekday count from the uploaded roster and labels it provisional; confirmation is not required to generate or download a warning-only result.
- Historical imports retain monthly assignments, the source workbook/sheet, employee identity where available, derived monthly pattern, and transport preference. The history screen shows imported/approved months and gaps.
- Historical completeness is reported. The annual pattern-change limit is checked when sufficient history exists; otherwise the result is provisional. The scheduler does not require a baseline confirmation before generating.
- Generation reports rule compliance, staffing coverage, history completeness, feasibility, and overall status separately. Warning-only `NEEDS_ATTENTION` runs can be downloaded as provisional. Mandatory rule conflicts or unsafe workbook structure block download.

## Enforced scheduling rules

- HDC3/HDC4/HDC5 day and weekend availability follows the shared facility rules; Asset activities receive no weekend assignments.
- Prior-month night workers cannot receive next-month night shift. Shift 3 is followed by a weekly off before a day shift; shift 2 does not require an extra off day. Weekly offs follow five consecutive working days and are aligned with weekends/public holidays where coverage allows.
- The source month's trailing work streak is carried into the target month, with at most five consecutive working days.
- Own-vehicle employees are preferred when choosing eligible staff. Cab-dependent employees remain eligible when needed.
- A deterministic, constraint-aware assignment heuristic selects eligible employees and spreads work. An independent validation pass reports coverage, rest, facility, consecutive-day, duplicate-assignment, and annual pattern-limit conflicts.
- Excel output preserves the source workbook package and adds a Validation Summary sheet with target month, status, staffing coverage, and findings.

## Data boundaries

- Exact future leave dates in an imported `Leave Details` tab and saved employee records are applied automatically. Confirmed public-holiday dates already in the project calendar are reused. The generator blocks leave dates and intervening public holidays/weekends when leave is adjacent; missing or unparseable dates are reported as limitations.
- The source workbook has no employee ID in the inspected copy. Employee database IDs are stable internal identifiers; imported employee codes are preserved when a workbook supplies them.
- Shift pattern is represented using the dominant numbered shift in the monthly assignment set, consistent with the project's existing primary-shift interpretation. The annual limit is checked only when a baseline and monthly history are sufficient for the target year.
- `Base file` values are not interpreted as staffing counts without an explicit mapping; the uploaded location roster is used to infer provisional counts instead.
- The scheduler uses deterministic candidate ranking and hard-constraint checks; it does not prove global optimality. Any uncovered configured demand is surfaced as `INFEASIBLE` and is never silently approved.

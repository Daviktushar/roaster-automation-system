# Roster automation rules and verification

## Configured through the application

- Staffing targets are saved by facility, shift, weekday/weekend, and effective month. A target can also override one specific date. The staffing screen shows source `Base file` rows and column locations for an administrator to confirm. When an unspecified count is needed for a draft, the generator infers the median same-weekday count from the source roster and requires confirmation before approval.
- Historical imports retain monthly assignments, the source workbook/sheet, employee identity where available, derived monthly pattern, and transport preference. The history screen shows imported/approved months and gaps.
- A separately audited baseline confirmation starts prospective pattern tracking. It never asserts earlier changes were zero. Pattern changes are recorded when a VALID roster is approved.
- Generation reports rule compliance, staffing coverage, history completeness, feasibility, and overall status separately. Only `VALID` runs can be approved and downloaded. `NEEDS_ATTENTION`, `INFEASIBLE`, and `INVALID` runs remain review-only.

## Enforced scheduling rules

- HDC3/HDC4/HDC5 day and weekend availability follows the shared facility rules; Asset activities receive no weekend assignments.
- Prior-month night workers cannot receive next-month night shift. Shift 3 is followed by a weekly off before a day shift; shift 2 does not require an extra off day. Weekly offs follow five consecutive working days and are aligned with weekends/public holidays where coverage allows.
- The source month's trailing work streak is carried into the target month, with at most five consecutive working days.
- Own-vehicle employees are preferred when choosing eligible staff. Cab-dependent employees remain eligible when needed.
- A deterministic, constraint-aware assignment heuristic selects eligible employees and spreads work. An independent validation pass reports coverage, rest, facility, consecutive-day, duplicate-assignment, and annual pattern-limit conflicts.
- Excel output preserves the source workbook package and adds a Validation Summary sheet with target month, status, staffing coverage, and findings.

## Data boundaries

- Planned leave is maintained on each employee profile, and target-month public holiday dates are entered in the generation form. The generator blocks leave dates and any intervening public holiday or weekend when leave is adjacent to it.
- The source workbook has no employee ID in the inspected copy. Employee database IDs are stable internal identifiers; imported employee codes are preserved when a workbook supplies them.
- Shift pattern is represented using the dominant numbered shift in the monthly assignment set, consistent with the project's existing primary-shift interpretation. The annual limit is checked only when a baseline and monthly history are sufficient for the target year.
- The `Base file` values are displayed for administrator confirmation, not interpreted as staffing counts without an explicit mapping.
- The scheduler uses deterministic candidate ranking and hard-constraint checks; it does not prove global optimality. Any uncovered configured demand is surfaced as `INFEASIBLE` and is never silently approved.

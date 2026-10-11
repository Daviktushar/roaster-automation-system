# Workbook findings

Inspected workbook: `Copy of HDC Oct'26 shift roster.xlsx` in `C:\Users\bhardwaj.tushar\Documents\Monthly Roaster\`. The source file was opened read-only. The supplied file differs from the `(4)` filename in the initial project brief.

## Worksheets (exact names and state)

| Exact worksheet name | State | Layout and notable fields |
|---|---|---|
| `Leave details` | Hidden | Name in column F; free-text leave dates in G; optional notes in H. Entries mostly refer to September 2026 and date formats vary. |
| `HDC3` | Visible | October 2026 roster, employee name, activity, days, shift/off/leave codes, shift rollups, `Own/Cab`, and `Activity`. Also has a shift-count summary and a second employee block. |
| `HDC4` | Visible | October 2026 roster, name, activities, days, rollups, location, distance, and working days. |
| `HDC5` | Visible | September 2026 roster, name, facility, days, and rollups. |
| `DL & SL` | Visible | September 2026 combined roster; includes HDC3 and combined HDC category labels. |
| `DD` | Hidden | September 2026 roster with activity and daily assignments. It has 30 day columns before the rollup columns. |
| `DD ` | Visible | Separate October 2026 roster with 31 day columns. The worksheet name includes a trailing space. |
| `OB & CE` | Hidden | September attendance-style sheet, with HDC3/HDC4/HDC5 labels and `WO` entries. |
| `Base file` | Visible | Includes a `2nd Shift` table and counts under HDC3/HDC4, plus other rows with incomplete headings. |

## Common fields and codes

- No dedicated employee ID field was observed; names are the available identifiers.
- Day numbers appear across the top of roster grids; weekday abbreviations appear on the next row. Not all sheets cover the same month or number of days.
- Observed daily values include `G`, `1`, `2`, `3`, `WO`, `PH`, `G/PL`, `2/PL`, and `LOA`; `Exit` also appears. These observed strings are not a complete codebook.
- Rollups include work/off totals, shift counts, leave counts, and shift amount formulas. HDC4 additionally includes location, distance, and working-day fields.
- HDC3 has a declared used range ending at row 1,048,571 while the meaningful content is around row 51. Importers must avoid iterating the formatting-only range.
- Workbook contains formulas, a calculation chain, hidden sheets, merged cells on `Base file`, and extensive formatting. No VBA project was found. Export must use a separate output file and preserve source workbook internals.

## Import decisions

- Preserve exact sheet names and visibility. Do not normalize `DD ` to `DD`.
- Store non-roster sheets as references; treat `Leave details` as raw review notes until dates can be confirmed.
- Keep general category labels without assuming each is a physical location. An unlabeled second column with consistent values such as `HDC3- CS`, `HDC3- Asset`, or `HDC5 Asset` is recognized as a combined HDC location/team field; otherwise column headers such as `Activity` or `Facility` determine how values are interpreted.
- Identify source month per sheet rather than assigning one month to the full workbook.
- Do not enforce `Base file` counts until their scope and meaning are confirmed.

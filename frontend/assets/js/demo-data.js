/* Values below are demo-only examples derived from inspected worksheet names and codes. */
window.ROSTER_DEMO = {
  categories: [
    { name: 'HDC3', kind: 'Monthly roster', details: 'Activities, daily assignments, own/cab, shift rollups', badge: 'H3', color: 'purple' },
    { name: 'HDC4', kind: 'Monthly roster', details: 'Activities, location, distance, working days', badge: 'H4', color: 'blue' },
    { name: 'HDC5', kind: 'Monthly roster', details: 'Facility assignments and daily shift codes', badge: 'H5', color: 'green' },
    { name: 'DL & SL', kind: 'Combined roster', details: 'Includes HDC3 and combined HDC categories', badge: 'DL', color: 'orange' },
    { name: 'DD', kind: 'Monthly roster', details: 'Activity categories and daily assignments', badge: 'DD', color: 'blue' },
    { name: 'DD (trailing space)', kind: 'Separate worksheet', details: 'Worksheet name is exactly “DD ” including its trailing space', badge: 'DD', color: 'purple' },
    { name: 'OB & CE', kind: 'Attendance view', details: 'HDC3, HDC4, HDC5 with off-day markers', badge: 'OB', color: 'green' }
  ],
  activities: ['3rd Party', 'DSRM', 'Expresslane', 'Imaging', 'Incidents', 'Innovation hub', 'META', 'NC Incidents', 'Products', 'SM & MD', 'VM'],
  employees: [
    { name: 'A. Ram', activity: 'NC Incidents', shifts: ['G','G','2','G','G','WO','WO'] },
    { name: 'B. Rao', activity: 'Expresslane', shifts: ['G','1','1','2','G','WO','WO'] },
    { name: 'C. Kumar', activity: 'Imaging', shifts: ['G','G/PL','G','G','G','WO','WO'] },
    { name: 'D. Shah', activity: 'DSRM', shifts: ['3','3','G','G','G','WO','WO'] },
    { name: 'E. Das', activity: 'META', shifts: ['2','2','WO','WO','1','1','3'] }
  ]
};

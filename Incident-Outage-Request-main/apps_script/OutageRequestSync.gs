/**
 * OutageRequestSync.gs
 *
 * This script IS the API -- it reads a region's "REPORTING TEMPLATES"
 * Google Sheet and hands the data straight back to whoever calls it.
 * Nothing gets pushed anywhere from here; the Incident & Outage Request
 * app calls this, gets rows back, and saves them itself.
 *
 * Handles three report types today (the same file, same deployment --
 * `?type=` picks which one):
 *   - outage_request : the "Outage Schedule" section
 *   - incident_report: the "Incident Report" section
 *   - daily_max_min  : the "Daily Max & Min Load and Voltage Profile" section
 * Daily Max/Min is read by fixed COLUMN POSITION rather than header text,
 * because that section's header row repeats "TIME (HRS)" six times (once
 * per max/min load, frequency, and voltage) -- header text alone can't
 * tell those columns apart. See its REPORT_TYPES entry below.
 *
 * SETUP (one-time, per region sheet):
 *   1. Open the region's sheet -> Extensions -> Apps Script.
 *   2. Delete the placeholder Code.gs content and paste this whole file in.
 *   3. Fill in CONFIG.REGION and CONFIG.TRIGGER_TOKEN below.
 *   4. Deploy -> New deployment -> select type "Web app".
 *        - Execute as: Me
 *        - Who has access: Anyone with the link
 *      Click Deploy, authorize the requested permissions, and copy the
 *      resulting Web App URL.
 *   5. Give that URL to whoever configures the Incident & Outage Request
 *      app's .env / Web App Settings page. That's the only setup step --
 *      no tab name to guess, no separate server to configure. The app's
 *      own dropdown lists the real tab names by calling this script.
 *   6. To reuse this file for another region, copy it into that region's
 *      sheet and change only CONFIG.REGION (and generate a fresh
 *      TRIGGER_TOKEN for it -- don't reuse one region's token elsewhere).
 *   7. If you already deployed an earlier version of this file for this
 *      region: paste this updated version over it in the same Apps Script
 *      project, then Deploy -> Manage deployments -> edit (pencil) the
 *      existing deployment -> New version -> Deploy. The Web App URL
 *      stays the same -- nothing to reconfigure in the app.
 */

const CONFIG = {
  // Exactly as it should be recorded against every row from this sheet,
  // e.g. 'ABUJA'.
  REGION: 'ABUJA',

  // Shared secret that authenticates the app's calls to THIS script
  // (checked in doGet below). Must match APPS_SCRIPT_TRIGGER_TOKEN in the
  // app's .env exactly.
  TRIGGER_TOKEN: 'PASTE THE TRIGGER TOKEN HERE',
};

const SYNC_ID_HEADER = 'Sync ID';

/**
 * One entry per report type. `columnMap` maps sheet header text -> JSON
 * field name the app expects (matched by header text, not column
 * position, since exact column order varies sheet to sheet).
 * `requiredHeaderMarkers` is how the header row for that section is found
 * -- needed because a tab can stack more than one report type on top of
 * each other, so a single column name (like "S/N") isn't a safe enough
 * signal on its own. `rowNumberHeader` is that section's own row-number
 * column, used to skip blank/instructional rows.
 */
const REPORT_TYPES = {
  outage_request: {
    rowNumberHeader: 'S/N',
    requiredHeaderMarkers: ['S/N', 'Application Date', 'Scheduled Start Date'],
    columnMap: {
      'S/N': 'S/N',
      'Application Date': 'Application Date',
      'Region': 'Region',
      'Station': 'Station',
      'Voltage Level': 'Voltage Level',
      'Equipment Name/Nomenclature': 'Equipment Name/Nomenclature',
      'Description of Work': 'Description of Work',
      'Scheduled Start Date': 'Scheduled Start Date',
      'Scheduled Start Time': 'Scheduled Start Time',
      'Scheduled End Date': 'Scheduled End Date',
      'Scheduled End Time': 'Scheduled End Time',
      'Estimated Duration (hrs)': 'Estimated Duration (hrs)',
      'Expected Load Affected (MW)': 'Expected Load Affected (MW)',
      'DISCO Affected': 'DISCO Affected',
      'Name of personnel informed': 'Name of personnel informed',
      'Name/Designation of Applicant': 'Name/Designation of Applicant',
      'Remarks/Notes': 'Remarks/Notes',
    },
  },
  incident_report: {
    // this sheet's row-number column is "S/NO", not "S/N" -- that
    // difference is also what tells the two sections apart when they're
    // stacked in the same tab.
    rowNumberHeader: 'S/NO',
    requiredHeaderMarkers: ['S/NO', 'DATE OF INCIDENT (DD/MM/YYYY)', 'TIME OUT'],
    columnMap: {
      'S/NO': 'S/NO',
      'DATE OF INCIDENT (DD/MM/YYYY)': 'DATE OF INCIDENT (DD/MM/YYYY)',
      'TIME OUT': 'TIME OUT',
      'LOCATION / STATION': 'LOCATION / STATION',
      'TYPE OF INCIDENT (FORCED, PLANNED, EMERGENCY)': 'TYPE OF INCIDENT (FORCED, PLANNED, EMERGENCY)',
      'DESCRIPTION OF INCIDENT': 'DESCRIPTION OF INCIDENT',
      'IMMEDIATE ACTION TAKEN': 'IMMEDIATE ACTION TAKEN',
      'AFFECTED STATIONS': 'AFFECTED STATIONS',
      'EQUIPMENT INVOLVED': 'EQUIPMENT INVOLVED',
      'LOAD INTERRUPTED (MW)': 'LOAD INTERRUPTED (MW)',
      'POTENTIAL IMPACT': 'POTENTIAL IMPACT',
      'ROOT CAUSE (PRELIMINARY)': 'ROOT CAUSE (PRELIMINARY)',
      'CORRECTIVE ACTION (PROPOSED)': 'CORRECTIVE ACTION (PROPOSED)',
      'DATE OF CLOSURE (DD/MM/YYYY)': 'DATE OF CLOSURE (DD/MM/YYYY)',
      'TIME IN': 'TIME IN',
      'STATUS': 'STATUS',
      'REMARKS': 'REMARKS',
    },
  },
  daily_max_min: {
    // No row-number column in this section at all -- a row counts as
    // real data if its DATE cell (found by position, see below) is
    // non-blank. `positional: true` switches pullRows() into
    // read-by-fixed-offset mode instead of the header-text lookup the
    // other two types use.
    positional: true,
    // "MAX (MW)" is never a merged/repeated header, unlike "DATE" (which
    // sits under a merged "MAXIMUM"/"MINIMUM" super-header and can read
    // back blank on the actual column-label row) -- anchor on this
    // instead and step back one column to find DATE.
    anchorHeader: 'MAX (MW)',
    anchorOffset: -1,
    requiredHeaderMarkers: ['MAX (MW)', 'MIN (MW)', 'TIME (HRS)'],
    // Left-to-right from the DATE column (offset 0), matching this
    // section's fixed real-world layout:
    //   DATE, MAX(MW), TIME, FREQ(HZ), TIME, MAX VOLTAGE, TIME,
    //   MIN(MW), TIME, FREQ(HZ), TIME, MIN VOLTAGE, TIME
    fields: [
      'DATE',
      'MAX_LOAD_MW', 'MAX_LOAD_TIME',
      'MAX_FREQ_HZ', 'MAX_FREQ_TIME',
      'MAX_VOLTAGE_KV', 'MAX_VOLTAGE_TIME',
      'MIN_LOAD_MW', 'MIN_LOAD_TIME',
      'MIN_FREQ_HZ', 'MIN_FREQ_TIME',
      'MIN_VOLTAGE_KV', 'MIN_VOLTAGE_TIME',
    ],
  },
};

/** Web app entry point. ?action=list_tabs, or ?action=pull&type=...&tab=... */
function doGet(e) {
  const token = e && e.parameter && e.parameter.token;
  if (token !== CONFIG.TRIGGER_TOKEN) {
    return jsonResponse({ ok: false, error: 'invalid token' });
  }
  const action = (e.parameter && e.parameter.action) || 'pull';
  try {
    if (action === 'list_tabs') {
      return jsonResponse({ ok: true, tabs: listTabs() });
    }
    const tabName = e.parameter && e.parameter.tab;
    if (!tabName) {
      return jsonResponse({ ok: false, error: 'missing "tab" parameter' });
    }
    const reportType = (e.parameter && e.parameter.type) || 'outage_request';
    const typeConfig = REPORT_TYPES[reportType];
    if (!typeConfig) {
      return jsonResponse({ ok: false, error: `unknown type "${reportType}"` });
    }
    return jsonResponse({ ok: true, ...pullRows(tabName, typeConfig) });
  } catch (err) {
    return jsonResponse({ ok: false, error: String(err) });
  }
}

/** Every tab name in this workbook, for the app's tab-picker dropdown. */
function listTabs() {
  return SpreadsheetApp.getActive().getSheets().map((s) => s.getName());
}

/**
 * Reads one tab's section for the given report type, backfills any
 * missing Sync ID cells, and returns the rows as plain JS objects --
 * nothing is POSTed anywhere from in here, the caller decides what to do
 * with the data.
 */
function pullRows(tabName, typeConfig) {
  const sheet = SpreadsheetApp.getActive().getSheetByName(tabName);
  if (!sheet) {
    throw new Error(`Tab "${tabName}" not found in this spreadsheet`);
  }

  const values = sheet.getDataRange().getValues();
  if (values.length < 2) {
    return { region: CONFIG.REGION, sheet_id: SpreadsheetApp.getActive().getId(), tab_name: tabName, rows: [] };
  }

  const headerRowIndex = findHeaderRowIndex(values, typeConfig.requiredHeaderMarkers);
  const header = values[headerRowIndex];

  const colIndex = {};
  header.forEach((h, i) => {
    const key = String(h).trim();
    if (key) colIndex[key] = i;
  });

  let syncIdCol = colIndex[SYNC_ID_HEADER];
  if (syncIdCol === undefined) {
    syncIdCol = header.length;
    sheet.getRange(headerRowIndex + 1, syncIdCol + 1).setValue(SYNC_ID_HEADER);
    colIndex[SYNC_ID_HEADER] = syncIdCol;
  }

  const rows = typeConfig.positional
    ? readPositionalRows(sheet, values, headerRowIndex, colIndex, syncIdCol, typeConfig)
    : readMappedRows(sheet, values, headerRowIndex, colIndex, syncIdCol, typeConfig);

  return {
    region: CONFIG.REGION,
    sheet_id: SpreadsheetApp.getActive().getId(),
    tab_name: tabName,
    rows: rows,
  };
}

/** Column-name lookup mode, used by outage_request and incident_report --
 * a row counts as real data if its own row-number column parses as a
 * plain number (skips blank rows and instructional/example rows). */
function readMappedRows(sheet, values, headerRowIndex, colIndex, syncIdCol, typeConfig) {
  const rowNumCol = colIndex[typeConfig.rowNumberHeader];
  const rows = [];
  for (let r = headerRowIndex + 1; r < values.length; r++) {
    const rowValues = values[r];
    const rowNumValue = rowNumCol !== undefined ? rowValues[rowNumCol] : null;
    if (rowNumValue === '' || rowNumValue === null || isNaN(Number(rowNumValue))) {
      continue;
    }
    const syncId = getOrCreateSyncId(sheet, rowValues, syncIdCol, r);
    const rowObj = { sync_id: syncId, region: CONFIG.REGION };
    Object.keys(typeConfig.columnMap).forEach((sheetHeader) => {
      const idx = colIndex[sheetHeader];
      if (idx !== undefined) {
        rowObj[typeConfig.columnMap[sheetHeader]] = formatCellValue(rowValues[idx]);
      }
    });
    rows.push(rowObj);
  }
  return rows;
}

/** Fixed-column-position mode, used by daily_max_min -- a row counts as
 * real data if the cell at its anchor-derived DATE column is non-blank. */
function readPositionalRows(sheet, values, headerRowIndex, colIndex, syncIdCol, typeConfig) {
  const anchorCol = colIndex[typeConfig.anchorHeader];
  if (anchorCol === undefined) {
    throw new Error(`Could not find anchor column "${typeConfig.anchorHeader}"`);
  }
  const dateCol = anchorCol + typeConfig.anchorOffset;
  const rows = [];
  for (let r = headerRowIndex + 1; r < values.length; r++) {
    const rowValues = values[r];
    const dateValue = rowValues[dateCol];
    if (dateValue === '' || dateValue === null || dateValue === undefined) {
      continue;
    }
    const syncId = getOrCreateSyncId(sheet, rowValues, syncIdCol, r);
    const rowObj = { sync_id: syncId, region: CONFIG.REGION };
    typeConfig.fields.forEach((fieldName, i) => {
      rowObj[fieldName] = formatCellValue(rowValues[dateCol + i]);
    });
    rows.push(rowObj);
  }
  return rows;
}

/** Reads the row's existing Sync ID, or mints and writes a new one. */
function getOrCreateSyncId(sheet, rowValues, syncIdCol, r) {
  let syncId = rowValues[syncIdCol];
  if (!syncId) {
    syncId = Utilities.getUuid();
    sheet.getRange(r + 1, syncIdCol + 1).setValue(syncId);
  }
  return syncId;
}

/**
 * Dates/times come through as JS Date objects when the sheet cell is
 * formatted as a date/time; format them as plain strings so the JSON
 * payload matches what a manually-typed 'DD/MM/YYYY' or 'HH:MM' cell
 * would send. Everything else passes through as-is (including sentinel
 * text like NIL/O-S/N-A -- the app decides what those mean, not this
 * script).
 */
function formatCellValue(value) {
  if (Object.prototype.toString.call(value) === '[object Date]') {
    const hasTime = value.getHours() !== 0 || value.getMinutes() !== 0 || value.getSeconds() !== 0;
    return hasTime
      ? Utilities.formatDate(value, Session.getScriptTimeZone(), 'HH:mm')
      : Utilities.formatDate(value, Session.getScriptTimeZone(), 'dd/MM/yyyy');
  }
  return value === '' || value === null || value === undefined ? null : value;
}

/** The header row must contain every marker in requiredMarkers -- this is
 * what tells one report type's section apart from another stacked in the
 * same tab. */
function findHeaderRowIndex(values, requiredMarkers) {
  for (let r = 0; r < values.length; r++) {
    const cells = values[r].map((c) => String(c).trim());
    if (requiredMarkers.every((marker) => cells.includes(marker))) {
      return r;
    }
  }
  throw new Error(`Could not find a header row (needs all of: ${requiredMarkers.join(', ')})`);
}

function jsonResponse(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

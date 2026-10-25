/**
 * Private bridge between GitHub Actions and the Social Leads Google Sheet.
 * Deploy as a Web App (execute as you; access: Anyone) and set Script Properties:
 *   SPREADSHEET_ID = the spreadsheet ID
 *   API_TOKEN = a long random secret also stored as GitHub secret GOOGLE_SHEETS_API_TOKEN
 */
function doPost(e) {
  try {
    var props = PropertiesService.getScriptProperties();
    var expected = props.getProperty('API_TOKEN');
    var body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    if (!expected || !body.token || body.token !== expected) {
      return json_({ok: false, error: 'unauthorized'});
    }
    var spreadsheetId = props.getProperty('SPREADSHEET_ID');
    if (!spreadsheetId) return json_({ok: false, error: 'SPREADSHEET_ID is not configured'});
    var ss = SpreadsheetApp.openById(spreadsheetId);
    var tabNames = {'Leads': 'Leads', 'Personal Profiles': 'Personal Profiles'};
    var tabName = tabNames[body.tab];
    if (!tabName) return json_({ok: false, error: 'invalid tab'});
    var sheet = ss.getSheetByName(tabName);
    if (!sheet) return json_({ok: false, error: 'missing tab: ' + tabName});
    if (body.action === 'read') return readTable_(sheet);
    if (body.action === 'upsert') return upsertTable_(sheet, body);
    return json_({ok: false, error: 'unsupported action'});
  } catch (err) {
    return json_({ok: false, error: String(err && err.message ? err.message : err)});
  }
}

function readTable_(sheet) {
  var values = sheet.getDataRange().getDisplayValues();
  if (!values || !values.length) return json_({ok: true, headers: [], rows: []});
  var headers = values[0].map(function(v) { return String(v || '').trim(); });
  var rows = [];
  for (var i = 1; i < values.length; i++) {
    var row = {};
    var nonEmpty = false;
    headers.forEach(function(h, j) {
      if (!h) return;
      row[h] = String(values[i][j] == null ? '' : values[i][j]);
      if (row[h].trim()) nonEmpty = true;
    });
    if (nonEmpty) rows.push(row);
  }
  return json_({ok: true, headers: headers, rows: rows});
}

function upsertTable_(sheet, body) {
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var incomingHeaders = (body.headers || []).map(function(h) { return String(h || '').trim(); }).filter(Boolean);
    var incomingRows = Array.isArray(body.rows) ? body.rows : [];
    var keyColumn = String(body.key_column || '').trim();
    var existing = sheet.getDataRange().getDisplayValues();
    var headers = existing.length ? existing[0].map(function(v) { return String(v || '').trim(); }).filter(Boolean) : [];
    if (!headers.length) headers = incomingHeaders.slice();
    incomingHeaders.forEach(function(h) { if (headers.indexOf(h) < 0) headers.push(h); });
    if (!headers.length) return json_({ok: false, error: 'no headers provided'});

    var oldRows = [];
    for (var i = 1; i < existing.length; i++) {
      var item = {};
      var hasData = false;
      headers.forEach(function(h, j) {
        item[h] = String((existing[i] || [])[j] == null ? '' : (existing[i] || [])[j]);
        if (item[h].trim()) hasData = true;
      });
      if (hasData) oldRows.push(item);
    }
    function keyFor(row) {
      var value = String((row && row[keyColumn]) || '').trim().toLowerCase().replace(/\/$/, '');
      if (!value && body.tab === 'Leads') {
        value = String(row.discovered_url || row.linkedin || row.website || row.company_name || '').trim().toLowerCase().replace(/\/$/, '');
      }
      return value;
    }
    var keyToIndex = {};
    oldRows.forEach(function(row, idx) {
      var key = keyFor(row);
      if (key && keyToIndex[key] === undefined) keyToIndex[key] = idx;
    });
    var added = 0, updated = 0;
    incomingRows.forEach(function(source) {
      var row = {};
      headers.forEach(function(h) { row[h] = source[h] == null ? '' : String(source[h]); });
      var key = keyFor(row);
      if (key && keyToIndex[key] !== undefined) {
        var target = oldRows[keyToIndex[key]];
        headers.forEach(function(h) {
          // Do not erase previously stored values with blanks from partial updates.
          if (String(row[h] || '').trim()) target[h] = row[h];
        });
        updated++;
      } else {
        oldRows.push(row);
        if (key) keyToIndex[key] = oldRows.length - 1;
        added++;
      }
    });
    var matrix = [headers].concat(oldRows.map(function(row) {
      return headers.map(function(h) { return row[h] == null ? '' : String(row[h]); });
    }));
    sheet.clearContents();
    sheet.getRange(1, 1, matrix.length, headers.length).setValues(matrix);
    return json_({ok: true, tab: body.tab, rows: oldRows.length, added: added, merged: updated});
  } finally {
    lock.releaseLock();
  }
}

function json_(value) {
  return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON);
}

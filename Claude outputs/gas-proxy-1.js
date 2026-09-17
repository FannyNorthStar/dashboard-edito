// Google Apps Script — CORS proxy + OpenAI proxy + Fluff Feedback store
// Deploy: Deploy → New deployment → Web app → Execute as Me → Anyone
// After updating this code: Deploy → Manage deployments → Edit → Version: New version → Deploy

// ========== FEEDBACK SHEET ==========
// Creates or gets the "FluffFeedback" tab in the active spreadsheet
// You must set FEEDBACK_SHEET_ID in Script Properties → the ID of a Google Sheet
// (the long string in the sheet URL between /d/ and /edit)

function getFeedbackSheet() {
  var id = PropertiesService.getScriptProperties().getProperty('FEEDBACK_SHEET_ID');
  if (!id) return null;
  var ss = SpreadsheetApp.openById(id);
  var sheet = ss.getSheetByName('FluffFeedback');
  if (!sheet) {
    sheet = ss.insertSheet('FluffFeedback');
    sheet.appendRow(['Timestamp', 'URL', 'Domain', 'Quote', 'Tests Failed', 'Verdict', 'Reviewer']);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

// ========== GET ==========
function doGet(e) {
  var action = e.parameter.action;

  // Read feedback data
  if (action === 'feedback') {
    var sheet = getFeedbackSheet();
    if (!sheet) {
      return ContentService.createTextOutput(JSON.stringify({error:'FEEDBACK_SHEET_ID not set in Script Properties'}))
        .setMimeType(ContentService.MimeType.JSON);
    }
    var data = sheet.getDataRange().getValues();
    var headers = data[0];
    var rows = [];
    for (var i = 1; i < data.length; i++) {
      var row = {};
      for (var j = 0; j < headers.length; j++) {
        row[headers[j].toLowerCase().replace(/\s+/g, '_')] = data[i][j];
      }
      rows.push(row);
    }
    return ContentService.createTextOutput(JSON.stringify({feedback: rows, count: rows.length}))
      .setMimeType(ContentService.MimeType.JSON);
  }

  // Default: CORS proxy
  var url = e.parameter.url;
  if (!url) return ContentService.createTextOutput('Missing url param');
  var resp = UrlFetchApp.fetch(url, {muteHttpExceptions:true, followRedirects:true});
  var out = ContentService.createTextOutput(resp.getContentText());
  out.setMimeType(ContentService.MimeType.TEXT);
  return out;
}

// ========== POST ==========
function doPost(e) {
  var body;
  try { body = JSON.parse(e.postData.contents); } catch(err) { body = {}; }

  // Write feedback row
  if (body.action === 'feedback') {
    var sheet = getFeedbackSheet();
    if (!sheet) {
      return ContentService.createTextOutput(JSON.stringify({error:'FEEDBACK_SHEET_ID not set in Script Properties'}))
        .setMimeType(ContentService.MimeType.JSON);
    }
    sheet.appendRow([
      new Date().toISOString(),
      body.url || '',
      body.domain || '',
      body.quote || '',
      body.tests_failed || '',
      body.verdict || '',
      body.reviewer || ''
    ]);
    return ContentService.createTextOutput(JSON.stringify({ok: true}))
      .setMimeType(ContentService.MimeType.JSON);
  }

  // Default: OpenAI proxy
  var key = PropertiesService.getScriptProperties().getProperty('OPENAI_KEY');
  if (!key) {
    return ContentService.createTextOutput(JSON.stringify({error:'OPENAI_KEY not set in Script Properties'}))
      .setMimeType(ContentService.MimeType.JSON);
  }

  var resp = UrlFetchApp.fetch('https://api.openai.com/v1/chat/completions', {
    method: 'post',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer ' + key
    },
    payload: e.postData.contents,
    muteHttpExceptions: true
  });

  var out = ContentService.createTextOutput(resp.getContentText());
  out.setMimeType(ContentService.MimeType.JSON);
  return out;
}

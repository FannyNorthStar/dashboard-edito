// Google Apps Script — CORS proxy + OpenAI proxy
// Deploy: Deploy → New deployment → Web app → Execute as Me → Anyone

function doGet(e) {
  var url = e.parameter.url;
  if (!url) return ContentService.createTextOutput('Missing url param');
  var resp = UrlFetchApp.fetch(url, {muteHttpExceptions:true, followRedirects:true});
  var out = ContentService.createTextOutput(resp.getContentText());
  out.setMimeType(ContentService.MimeType.TEXT);
  return out;
}

function doPost(e) {
  var key = PropertiesService.getScriptProperties().getProperty('OPENAI_KEY');
  if (!key) {
    return ContentService.createTextOutput(JSON.stringify({error:'OPENAI_KEY not set in Script Properties'}))
      .setMimeType(ContentService.MimeType.JSON);
  }

  var body = e.postData.contents;
  var resp = UrlFetchApp.fetch('https://api.openai.com/v1/chat/completions', {
    method: 'post',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer ' + key
    },
    payload: body,
    muteHttpExceptions: true
  });

  var out = ContentService.createTextOutput(resp.getContentText());
  out.setMimeType(ContentService.MimeType.JSON);
  return out;
}

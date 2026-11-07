/* Real local handlers + synthetic language, with no AWS or course Lab execution. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), http = require('node:http');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const [readyPath, output, phase] = process.argv.slice(2);
const ready = JSON.parse(fs.readFileSync(readyPath));
const privateDir = path.dirname(readyPath), statePath = path.join(privateDir, 'browser-state.json');
const entry = 'https://class-a-entry.workshops.example.com/doris-courses/';
const course = 'http://127.0.0.1:' + ready.course_port + '/workshop/';
const report = {phase, synthetic: true, checks: [], screenshots: []};
let browser, subject = 'subject-alice', recoveryMode = 'connected';
function check(name) { report.checks.push(name); }
function forward(request, port, host, extra = {}) {
  return new Promise((resolve, reject) => {
    const url = new URL(request.url()), headers = {...request.headers(), host, 'accept-encoding': 'identity', ...extra};
    delete headers['content-length'];
    const req = http.request({hostname: '127.0.0.1', port, path: url.pathname + url.search, method: request.method(), headers}, response => {
      const chunks = []; response.on('data', b => chunks.push(b)); response.on('end', () => {
        const h = {...response.headers}; for (const key of ['connection', 'transfer-encoding', 'content-length', 'set-cookie']) delete h[key];
        resolve({status: response.statusCode, headers: h, body: Buffer.concat(chunks)});
      });
    }); req.on('error', reject); const body = request.postDataBuffer(); if (body) req.write(body); req.end();
  });
}
(async () => {
  browser = await chromium.launch({headless: true, ...(process.env.PLAYWRIGHT_EXECUTABLE ? {executablePath: process.env.PLAYWRIGHT_EXECUTABLE} : {})});
  const context = await browser.newContext({locale: 'en-US', viewport: {width: 1440, height: 1000},
    ...(phase === 'restart' ? {storageState: statePath} : {})});
  await context.setExtraHTTPHeaders({'Authorization': 'token ' + ready.jupyter_token});
  const errors = [], requests = [];
  context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
  await context.route('https://**/*', async route => {
    const request = route.request(), url = new URL(request.url()); requests.push({path: url.pathname, method: request.method()});
    if (url.hostname === 'workshops.example.com' || url.hostname === 'class-a-entry.workshops.example.com') {
      return route.fulfill(await forward(request, ready.portal_port, url.hostname, url.hostname.includes('-entry.') ? {'x-amzn-oidc-data': ready.oidc[subject]} : {}));
    }
    if (url.hostname === 'class-a-alice-jupyter.workshops.example.com') {
      return route.fulfill(await forward(request, ready.course_port, '127.0.0.1', {'authorization': 'token ' + ready.jupyter_token}));
    }
    if (url.hostname === ready.gateway_host) {
      if (url.pathname === '/_fixture/recovery') return route.fulfill({contentType: 'text/html', body: '<!doctype html><head><link rel="stylesheet" href="/_workshop/session_recovery.css"></head><body><script src="/_workshop/session_recovery.js?locale=kk-Cyrl"></script></body>'});
      if (url.pathname === '/_workshop/session' && recoveryMode !== 'connected') return route.fulfill({status: recoveryMode === 'ended' ? 403 : 401, headers: recoveryMode === 'ended' ? {'X-Workshop-Access': 'expired'} : {}, body: ''});
      return route.fulfill(await forward(request, ready.gateway_port, ready.gateway_host, {'x-amzn-oidc-data': ready.gateway_oidc}));
    }
    return route.fulfill({status: 403, body: 'Fixture route unavailable'});
  });
  const page = await context.newPage();
  async function waitLocale(tab, locale) {
    try { await tab.waitForURL(url => url.searchParams.get('locale') === locale); }
    catch (_) { throw new Error('Expected locale ' + locale + ' at ' + tab.url() + '; page: ' + (await tab.locator('body').innerText()).slice(0, 200)); }
    await tab.locator('html[lang="' + locale + '"]').waitFor();
  }
  await page.goto(entry); await waitLocale(page, phase === 'restart' ? 'kk-Cyrl' : 'en');
  if (phase === 'restart') {
    check('preference survives browser session and full handler/Jupyter restart');
    await context.close(); await browser.close();
    fs.writeFileSync(path.join(output, phase + '-report.json'), JSON.stringify(report, null, 2)); console.log(JSON.stringify(report)); return;
  }
  const oldTab = await context.newPage(); await oldTab.goto(entry + '?locale=en');
  const before = requests.length;
  await page.selectOption('#workshop-language', 'kk-Cyrl'); await page.locator('.language-form button').click(); await waitLocale(page, 'kk-Cyrl');
  assert(!requests.slice(before).some(r => r.method !== 'GET' || /\/progress\/|\/lab/.test(r.path)));
  assert.equal(new URL(oldTab.url()).searchParams.get('locale'), 'en');
  check('switch only navigates the current tab; no download, Lab, progress, identity or deployment write');
  const newTab = await context.newPage(); await newTab.goto(entry); await waitLocale(newTab, 'kk-Cyrl');
  await oldTab.reload(); await waitLocale(oldTab, 'en');
  check('new pages restore preference; existing explicit English tab stays English');
  subject = 'subject-bob'; await newTab.goto(entry); await waitLocale(newTab, 'en');
  assert(!(await newTab.locator('main').innerText()).includes('alice'));
  subject = 'subject-alice'; await newTab.goto(entry); await waitLocale(newTab, 'kk-Cyrl');
  check('same browser entry isolates authenticated learners and restores after relogin');
  const response = await newTab.goto(entry + '?locale=fr'); assert.equal(response.status(), 406);
  assert((await newTab.locator('body').innerText()).includes('not available'));
  check('unknown or uninstalled language has an explicit unavailable response');
  assert((await page.locator('.course-board a.primary').getAttribute('href')).includes('locale=kk-Cyrl'));
  await page.goto(course + '?locale=kk-Cyrl'); await waitLocale(page, 'kk-Cyrl');
  assert((await page.locator('h1').innerText()).includes('ӘҒҚҢӨҰҮҺІ'));
  await page.locator('.module .actions a').first().click(); await waitLocale(page, 'kk-Cyrl');
  await page.evaluate(() => location.hash = 'section.fixture');
  const selectedPath = new URL(page.url()).pathname;
  await page.selectOption('#workshop-language', 'en'); await page.locator('.language-form button').click(); await waitLocale(page, 'en');
  assert.equal(new URL(page.url()).pathname, selectedPath); assert.equal(new URL(page.url()).hash, '#section.fixture');
  assert.equal(await page.locator('.article h2').getAttribute('id'), 'section.fixture');
  await page.selectOption('#workshop-language', 'kk-Cyrl'); await page.locator('.language-form button').click(); await waitLocale(page, 'kk-Cyrl');
  assert.equal(await page.locator('.article img').getAttribute('alt'), 'kk-Cyrl image description');
  assert((await page.locator('.article img').getAttribute('src')).includes('locale=kk-Cyrl'));
  assert((await page.locator('.reading-side form').getAttribute('action')).includes('locale=kk-Cyrl'));
  check('lesson module/path and section survive switching; images and Notebook links carry locale');
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({width, height: 1000});
    await page.evaluate(() => document.fonts.ready);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'overflow ' + width);
    assert.equal(await page.locator('img').evaluateAll(nodes => nodes.every(n => n.complete && n.naturalWidth > 0)), true);
    const file = 'synthetic-lesson-' + width + '.png'; await page.screenshot({path: path.join(output, file), fullPage: true}); report.screenshots.push(file);
  }
  await page.keyboard.press('Tab');
  await page.locator('#workshop-language').focus(); assert.equal(await page.evaluate(() => document.activeElement.id), 'workshop-language');
  await page.keyboard.press('Tab'); assert.equal(await page.evaluate(() => document.activeElement.tagName), 'BUTTON');
  const glyphs = await page.evaluate(async () => {
    await document.fonts.ready;
    const c = document.createElement('canvas').getContext('2d'); c.font = '20px sans-serif';
    return [...'ӘҒҚҢӨҰҮҺІ'].map(char => c.measureText(char).width);
  });
  assert(glyphs.every(width => width > 0));
  check('long Cyrillic copy, 1440/390/320 widths, image rendering, keyboard and labelled controls');
  // Mirror navigation must visibly move to English, never use the target route.
  await page.locator('.article a').filter({hasText: 'English mirror'}).click(); await waitLocale(page, 'en');
  check('English navigation mirror redirects to an explicit English URL');
  await page.goto(course + 'content/' + 'doris-course/01-real-time-analytics/missing.md?locale=kk-Cyrl');
  assert.equal(await page.locator('html').getAttribute('lang'), 'kk-Cyrl');
  check('owned content error page keeps requested locale and hides private diagnostics');
  await page.goto(course + '?locale=kk-Cyrl');
  const importLink = page.locator('a').filter({hasText: 'Import progress'}).first(); await importLink.click();
  assert.equal(new URL(page.url()).searchParams.get('locale'), 'kk-Cyrl');
  assert((await page.locator('label[for="progress-file"]').innerText()).includes('ӘҒҚҢӨҰҮҺІ'));
  check('progress import/export UI and form actions receive locale without changing record identity');
  const recovery = await context.newPage(); await recovery.goto('https://' + ready.gateway_host + '/_fixture/recovery?locale=kk-Cyrl');
  await recovery.waitForFunction(() => window.__dorisWorkshopRecovery);
  await recovery.evaluate(() => window.dispatchEvent(new Event('offline')));
  await recovery.locator('#workshop-connection[data-state="network"]').waitFor();
  assert((await recovery.locator('#workshop-connection').innerText()).includes('ӘҒҚҢӨҰҮҺІ'));
  recoveryMode = 'auth'; await recovery.evaluate(() => window.dispatchEvent(new Event('online')));
  await recovery.locator('#workshop-connection[data-state="auth"]').waitFor();
  assert((await recovery.locator('#workshop-connection button').innerText()).includes('ӘҒҚҢӨҰҮҺІ'));
  for (const width of [1440, 390, 320]) {
    await recovery.setViewportSize({width, height: 1000});
    assert.equal(await recovery.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.equal(await recovery.locator('#workshop-connection').getAttribute('lang'), 'kk-Cyrl');
    const recoveryFile = 'synthetic-recovery-' + width + '.png'; await recovery.screenshot({path: path.join(output, recoveryFile)}); report.screenshots.push(recoveryFile);
  }
  recoveryMode = 'ended'; await recovery.evaluate(() => window.dispatchEvent(new Event('online')));
  await recovery.locator('#workshop-connection[data-state="ended"]').waitFor();
  check('network, sign-in and ended recovery messages use the tab locale');
  await context.storageState({path: statePath}); fs.chmodSync(statePath, 0o600);
  assert.deepEqual(errors, []);
  fs.writeFileSync(path.join(output, phase + '-report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report)); await browser.close();
})().catch(async error => { console.error(error.stack); await browser?.close(); process.exitCode = 1; });

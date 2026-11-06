/* Real JupyterLab/browser acceptance. The loopback fixture simulates ALB auth only. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const [origin, output, soakArg] = process.argv.slice(2);
assert.equal(new URL(origin).hostname, 'localhost');
const soakSeconds = Number(soakArg || 0);
const report = {scope: 'real local JupyterLab and gateway; simulated ALB authentication', phases: []};
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser;
function checkpoint(name, data = {}) {
  report.phases.push({name, at: new Date().toISOString(), ...data});
  fs.writeFileSync(path.join(output, 'browser-report.json'), JSON.stringify(report, null, 2));
  console.log('RECOVERY_PHASE ' + name);
}
async function snapshot(page) {
  return page.evaluate(() => {
    const widget = window.jupyterapp.shell.currentWidget;
    const kernel = widget?.sessionContext?.session?.kernel;
    return {kernel: kernel?.id, status: kernel?.status, connection: kernel?.connectionStatus,
      dirty: widget?.context?.model?.dirty, source: widget?.content?.model?.cells.get(0).sharedModel.getSource(),
      banner: document.querySelector('#workshop-connection')?.dataset.state,
      connectionModal: [...document.querySelectorAll('.jp-Dialog')].some(x => x.textContent.includes('Server Connection Error')),
      modal: [...document.querySelectorAll('.jp-Dialog')].some(x => x.textContent.includes('Kernel died unexpectedly'))};
  });
}
async function execute(page, code, expressions = {}) {
  return page.evaluate(async ({code, expressions}) => {
    const kernel = window.jupyterapp.shell.currentWidget.sessionContext.session.kernel;
    return (await kernel.requestExecute({code, silent: true, store_history: false, user_expressions: expressions}).done).content;
  }, {code, expressions});
}
async function connected(page, timeout = 60000) {
  await page.waitForFunction(() => window.jupyterapp?.shell.currentWidget?.sessionContext?.session?.kernel?.connectionStatus === 'connected', null, {timeout});
}
(async () => {
  browser = await chromium.launch({headless: true});
  const context = await browser.newContext({ignoreHTTPSErrors: true, viewport: {width: 1440, height: 1000}});
  context.setDefaultTimeout(45000);
  const page = await context.newPage();
  const control = async values => {
    const response = await context.request.post(origin + '/_fixture/control', {data: values});
    assert(response.ok());
  };
  await page.goto(origin + '/lab/tree/idle.ipynb');
  await connected(page);
  assert(await page.evaluate(() => window.__dorisWorkshopRecovery));
  await execute(page, "sentinel='preserved'; executions=1; import os", {pid: 'os.getpid()'});
  const baseline = await snapshot(page);
  const pid = (await execute(page, '', {pid: 'os.getpid()'})).user_expressions.pid.data['text/plain'];
  await page.evaluate(() => window.jupyterapp.shell.currentWidget.content.model.cells.get(0).sharedModel.setSource('print("unsaved recovery draft")'));
  checkpoint('initial', {baseline, pid});
  await execute(page, 'import time; time.sleep(12); executions += 1');
  await connected(page);
  assert.equal((await snapshot(page)).kernel, baseline.kernel);
  assert.equal((await snapshot(page)).modal, false);
  assert.equal((await execute(page, '', {count: 'executions'})).user_expressions.count.data['text/plain'], '2');
  checkpoint('active_execution_crosses_socket_expiry');
  await control({allowed: false, mode: 401});
  await page.waitForSelector('#workshop-connection[data-state="auth"]', {timeout: 40000});
  let failed = await snapshot(page);
  assert.equal(failed.kernel, baseline.kernel);
  assert.notEqual(failed.status, 'dead');
  assert.equal(failed.modal, false);
  assert.equal(failed.connectionModal, false);
  assert.equal(failed.source, 'print("unsaved recovery draft")');
  await page.screenshot({path: path.join(output, 'auth-required.png')});
  await page.getByRole('button', {name: 'Sign in to reconnect'}).click();
  await connected(page);
  await page.waitForSelector('#workshop-connection[hidden]', {state: 'attached'});
  assert.equal((await execute(page, '', {sentinel: 'sentinel', pid: 'os.getpid()', count: 'executions'})).user_expressions.pid.data['text/plain'], pid);
  checkpoint('401_and_popup_recovery_preserve_process_and_unsaved_edit', {after: await snapshot(page)});
  const secondContext = await browser.newContext({ignoreHTTPSErrors: true});
  secondContext.setDefaultTimeout(45000);
  const second = await secondContext.newPage();
  await second.goto(origin + '/lab/workspaces/browser-two/tree/idle.ipynb');
  await connected(second);
  assert.equal((await snapshot(second)).kernel, baseline.kernel);
  await control({allowed: false, mode: 302});
  await page.waitForSelector('#workshop-connection[data-state="auth"]', {timeout: 40000});
  assert.equal((await snapshot(page)).modal, false);
  await page.getByRole('button', {name: 'Sign in to reconnect'}).click();
  await connected(page); await connected(second);
  assert.equal((await snapshot(second)).kernel, baseline.kernel);
  checkpoint('redirected_login_and_two_browser_recovery');
  // Authentication failures must never silently repeat a write or restart request.
  await control({allowed: false, mode: 403});
  const write = await page.evaluate(async () => {
    try { await fetch('/api/contents/never-replayed.txt', {method: 'PUT', body: '{}'}); return 'unexpected'; }
    catch (error) { return error.name; }
  });
  assert.equal(write, 'TypeError');
  const cancelled = await page.evaluate(async () => {
    const controller = new AbortController();
    const request = fetch('/api/status', {signal: controller.signal});
    controller.abort();
    try { await request; return 'unexpected'; } catch (error) { return error.name; }
  });
  assert.equal(cancelled, 'AbortError');
  await control({allowed: true});
  await page.evaluate(() => window.dispatchEvent(new Event('online')));
  await connected(page);
  const stats = await (await context.request.get(origin + '/_fixture/stats')).json();
  assert(!stats.requests.some(x => x.path === '/api/contents/never-replayed.txt'));
  checkpoint('403_does_not_replay_writes');
  const missing = await page.evaluate(async () => (await fetch('/api/kernels/00000000-0000-4000-8000-000000000000')).status);
  assert.equal(missing, 404);
  checkpoint('missing_kernel_404_remains_distinct_from_auth_failure');
  if (soakSeconds) {
    await control({jwt_seconds: 900, session_seconds: 3600, mode: 401});
    const start = Date.now();
    checkpoint('soak_started', {seconds: soakSeconds});
    while (Date.now() - start < soakSeconds * 1000) {
      await pause(15000);
      const current = await snapshot(page);
      assert.equal(current.kernel, baseline.kernel); assert.equal(current.modal, false); assert.notEqual(current.status, 'dead');
      if (current.banner === 'auth') await page.getByRole('button', {name: 'Sign in to reconnect'}).click();
      fs.writeFileSync(path.join(output, 'soak-progress.json'), JSON.stringify({elapsed_seconds: Math.floor((Date.now()-start)/1000), current}, null, 2));
    }
    await connected(page);
    const values = (await execute(page, '', {pid: 'os.getpid()', count: 'executions', sentinel: 'sentinel'})).user_expressions;
    assert.equal(values.pid.data['text/plain'], pid); assert.equal(values.count.data['text/plain'], '2');
    assert.equal(values.sentinel.data['text/plain'], "'preserved'");
    checkpoint('soak_passed', {elapsed_seconds: Math.floor((Date.now()-start)/1000), values});
  }
  await page.setViewportSize({width: 390, height: 844});
  await control({allowed: false, mode: 401});
  await page.evaluate(() => window.dispatchEvent(new Event('online')));
  await page.waitForSelector('#workshop-connection[data-state="auth"]');
  await page.screenshot({path: path.join(output, 'auth-required-mobile.png')});
  await control({allowed: true, ended: true});
  await page.evaluate(() => window.dispatchEvent(new Event('online')));
  await page.waitForSelector('#workshop-connection[data-state="ended"]');
  assert.equal(await page.getByRole('button', {name: 'Sign in to reconnect'}).isVisible(), false);
  checkpoint('workshop_expiry_blocks_reauthentication');
  report.passed = true;
  fs.writeFileSync(path.join(output, 'browser-report.json'), JSON.stringify(report, null, 2));
})().catch(error => {
  report.passed = false; report.error = error.stack;
  fs.writeFileSync(path.join(output, 'browser-report.json'), JSON.stringify(report, null, 2));
  console.error(error); process.exitCode = 1;
}).finally(async () => { if (browser) await browser.close(); });

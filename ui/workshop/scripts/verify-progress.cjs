/* Real loopback Jupyter and widget interaction. Credentials live only in a private fixture. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs'), path = require('path'), assert = require('assert/strict');
const fixture = JSON.parse(fs.readFileSync(process.argv[2])), output = process.argv[3], phase = process.argv[4];
let browser;
(async () => {
  browser = await chromium.launch({headless:true, ...(process.env.PLAYWRIGHT_EXECUTABLE ? {executablePath:process.env.PLAYWRIGHT_EXECUTABLE} : {})});
  const errors = [], checks = [], screenshots = [];
  async function login(which) {
    const context = await browser.newContext({viewport:{width:1440,height:1050}, acceptDownloads:true});
    const page = await context.newPage();
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', m => {if(m.type()==='error' && /Content Security Policy|Refused to/i.test(m.text())) errors.push(m.text())});
    await page.goto(fixture[which].base + '/login?token=' + encodeURIComponent(fixture[which].token));
    await page.goto(fixture[which].base + '/workshop/');
    await page.getByRole('heading', {name:'Courses & Labs',exact:true}).waitFor();
    return {context,page};
  }
  async function record(page, which='a') {
    const response = await page.request.get(fixture[which].base + '/workshop/progress/export');
    assert.equal(response.status(),200);
    return (await response.json()).records.find(r => r.course_slug==='real-time-analytics' && r.module_id==='module-1');
  }
  async function snapshot(page,name) {
    await page.evaluate(() => document.fonts.ready);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    assert.equal(overflow,false,name+' horizontal overflow');
    await page.screenshot({path:path.join(output,name+'.png'),fullPage:false});
    screenshots.push(name+'.png');
  }
  let {context,page} = await login('a');
  const base = fixture.a.base, mark = '/workshop/progress/real-time-analytics/module-1/mark';
  if(phase==='interactive') {
    assert.equal(await record(page),undefined);
    const row = () => page.locator('.module').filter({has:page.locator('form[action^="'+mark+'"]')});
    await row().getByRole('link',{name:'Read lesson'}).click();
    assert.equal(await record(page),undefined,'opening a lesson grants no credit');
    await page.getByRole('button',{name:'Mark as read',exact:true}).click();
    assert.equal((await record(page)).course_read,true);
    await page.getByRole('region',{name:'Module progress',exact:true}).scrollIntoViewIfNeeded();
    await snapshot(page,'lesson-read-desktop');
    await page.setViewportSize({width:390,height:900});
    await snapshot(page,'lesson-read-mobile');
    await page.setViewportSize({width:1440,height:1050});
    await page.goto(base+'/workshop/');
    const labPromise = context.waitForEvent('page');
    await row().getByRole('button',{name:'Open Lab'}).click();
    const labPage = await labPromise;
    await labPage.locator('.jp-Notebook').waitFor({timeout:60000});
    await labPage.close();
    await page.goto(base+'/workshop/');
    await row().getByRole('button',{name:'Mark Lab complete',exact:true}).click();
    assert.equal((await record(page)).lab_declared_complete,true);
    const quizPromise = context.waitForEvent('page');
    await row().getByRole('link',{name:'Open Quiz'}).click();
    const quizPage = await quizPromise;
    await quizPage.locator('.jp-Notebook').waitFor({timeout:60000});
    await quizPage.locator('.jp-CodeCell .cm-content').first().click();
    await quizPage.keyboard.press('Shift+Enter');
    await quizPage.locator('.doris-quiz-question').waitFor({timeout:60000});
    if(fixture.bilingual) {
      await quizPage.getByRole('radio',{name:fixture.correct[0],exact:true}).check();
      await quizPage.getByRole('button',{name:'Synthetic Cyrillic',exact:true}).click();
      await quizPage.getByRole('radio',{name:'Ә '+fixture.correct[0],exact:true}).waitFor();
      assert.equal(await quizPage.getByRole('radio',{name:'Ә '+fixture.correct[0],exact:true}).isChecked(),true);
      assert.equal((await (await page.request.get(base+'/workshop/progress/export')).json()).attempts.length,0);
      checks.push('synthetic presentation switch preserves an unsubmitted stable option and creates no attempt');
      await snapshot(quizPage,'quiz-synthetic-draft-preserved');
    }
    async function answer(values, partialCheck=false) {
      for(let i=0;i<values.length;i++) {
        await quizPage.locator('.doris-quiz-kicker').filter({hasText:'Question '+(i+1)+' of '}).waitFor();
        await quizPage.getByRole('radio',{name:values[i],exact:true}).check();
        await quizPage.getByRole('button',{name:/Submit answer/}).click();
        if(i===0 && partialCheck) assert.equal((await record(page)).best_total,0,'partial answers must not save a score');
        await quizPage.getByRole('button',{name:i===values.length-1?/View results/:/Next question/}).click();
      }
    }
    await answer(fixture.correct.map(v=>fixture.bilingual?'Ә '+v:v),true);
    await quizPage.getByText('Progress saved. Quiz passed;',{exact:false}).waitFor();
    await snapshot(quizPage,'quiz-passed');
    assert.equal((await record(page)).quiz_passed,true);
    const firstHistory = (await (await page.request.get(base+'/workshop/progress/export')).json()).attempts;
    assert.equal(firstHistory.length,1);
    assert.equal(firstHistory[0].locale,fixture.bilingual?'kk-Cyrl':'en');
    if(fixture.bilingual) await quizPage.getByRole('button',{name:'English',exact:true}).click();
    assert.equal((await (await page.request.get(base+'/workshop/progress/export')).json()).attempts.length,1);
    await quizPage.getByRole('button',{name:/Try again/}).click();
    await answer(fixture.wrong);
    await quizPage.getByText('Your earlier Quiz pass and best score are retained.',{exact:false}).waitFor();
    assert.equal((await record(page)).best_correct,fixture.correct.length);
    await snapshot(quizPage,'quiz-retry-preserves-pass');
    const kernels = await (await page.request.get(base+'/api/kernels')).json();
    const xsrf = (await context.cookies(base)).find(c=>c.name==='_xsrf').value;
    for(const kernel of kernels) {
      const response = await page.request.post(base+'/api/kernels/'+kernel.id+'/restart',{headers:{'X-XSRFToken':xsrf,'Origin':base}});
      assert.equal(response.status(),200);
    }
    assert.equal((await record(page)).quiz_passed,true);
    checks.push('explicit marks, lab opening, complete widget grading, preserved pass, kernel restart');
    await quizPage.close();
    await page.goto(base+'/workshop/');
    await row().getByRole('button',{name:'Undo Lab completion',exact:true}).click();
    assert.equal((await record(page)).lab_declared_complete,false);
    await row().getByRole('button',{name:'Mark Lab complete',exact:true}).click();
    // A second tab updates a different field without replacing the Quiz write.
    const second = await context.newPage();
    await second.goto(base+'/workshop/');
    await second.locator('form[action^="'+mark+'"]').getByRole('button',{name:'Undo Lab completion',exact:true}).click();
    await second.close();
    await page.reload();
    await row().getByRole('button',{name:'Mark Lab complete',exact:true}).click();
    await page.getByRole('heading',{name:'Course progress: 10.0%',exact:true}).waitFor();
    for(const width of [1440,390,320]) {
      await page.setViewportSize({width,height:1000});
      await snapshot(page,'outline-'+width);
      await row().scrollIntoViewIfNeeded();
      await snapshot(page,'module-credit-'+width);
      await page.evaluate(()=>scrollTo(0,0));
    }
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('link',{name:'Download progress JSON',exact:true}).click();
    const download = await downloadPromise;
    const file = path.join(output,'transfer.json');
    await download.saveAs(file);
    const exported = JSON.parse(fs.readFileSync(file));
    assert.equal(exported.schema_version,2);
    assert.equal(exported.attempts.length,2);
    assert.equal(exported.attempts[0].trust,'self-study');
    await page.goto(base+'/logout');
    await context.close();
    ({context,page} = await login('a'));
    assert.equal((await record(page)).quiz_passed,true);
    checks.push('two tabs, logout and independent browser login, desktop and mobile layout, native JSON download');
    const b = await login('b');
    assert.equal(await record(b.page,'b'),undefined);
    await b.page.getByRole('link',{name:'Import progress',exact:true}).click();
    await b.page.getByLabel('Progress JSON file (up to 1 MiB)').setInputFiles(file);
    await b.page.getByRole('button',{name:'Preview import',exact:true}).click();
    await b.page.getByRole('button',{name:'Confirm import',exact:true}).waitFor();
    assert.equal(await record(b.page,'b'),undefined,'preview cannot write progress');
    await b.page.setViewportSize({width:390,height:900});
    await snapshot(b.page,'import-preview-mobile');
    await b.page.setViewportSize({width:1440,height:1050});
    await snapshot(b.page,'import-preview-desktop');
    await b.page.getByRole('button',{name:'Confirm import',exact:true}).click();
    await b.page.getByText('Import saved for 1 matching module.',{exact:false}).waitFor();
    assert.equal((await record(b.page,'b')).quiz_passed,true);
    assert.deepEqual((await (await b.page.request.get(fixture.b.base+'/workshop/progress/export')).json()).attempts,exported.attempts);
    // Reimport an older record without reducing current completion.
    await b.page.goto(fixture.b.base+'/workshop/progress/import');
    await b.page.getByLabel('Progress JSON file (up to 1 MiB)').setInputFiles(file);
    await b.page.getByRole('button',{name:'Preview import',exact:true}).click();
    await b.page.getByRole('button',{name:'Confirm import',exact:true}).click();
    assert.equal((await record(b.page,'b')).best_correct,fixture.correct.length);
    await b.page.goto(fixture.b.base+'/workshop/');
    await b.page.getByRole('heading',{name:'Course progress: 10.0%',exact:true}).waitFor();
    await snapshot(b.page,'second-workshop-restored');
    await b.page.getByRole('link',{name:'Import progress',exact:true}).click();
    await b.page.getByLabel('Progress JSON file (up to 1 MiB)').setInputFiles({name:'invalid.json',mimeType:'application/json',buffer:Buffer.from('{"schema_version":2}')});
    await b.page.getByRole('button',{name:'Preview import',exact:true}).click();
    await b.page.getByRole('alert').waitFor();
    await snapshot(b.page,'import-error');
    await b.context.close();
    checks.push('new environment starts empty, preview has no writes, confirmed import, idempotent repeat and invalid file feedback');
  } else {
    assert.equal((await record(page)).quiz_passed,true);
    assert.equal((await record(page)).lab_declared_complete,true);
    const b = await login('b');
    assert.equal((await record(b.page,'b')).quiz_passed,true);
    await page.getByRole('heading',{name:'Course progress: 10.0%',exact:true}).waitFor();
    await snapshot(page,'after-jupyter-restart');
    await b.context.close();
    checks.push('both Jupyter processes restarted, rotated tokens and fresh browser sessions, both learning records retained');
  }
  await context.close();
  const report = {ok:errors.length===0,phase,checks,screenshots,errors,scope:'Local Jupyter processes and real course Quiz widget; fixture Lab cells only, no AWS or course-Lab qualification'};
  fs.writeFileSync(path.join(output,phase+'-report.json'),JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify(report));
  await browser.close();
  if(!report.ok) process.exitCode=1;
})().catch(async e=>{
  console.error(e.message);
  if(browser) {
    let i=0;
    for(const context of browser.contexts()) for(const page of context.pages()) {
      await page.screenshot({path:path.join(output,'failed-'+phase+'-'+i+'.png')}).catch(()=>{});
      fs.writeFileSync(path.join(output,'failed-'+phase+'-'+i+'.txt'),await page.locator('body').innerText().catch(()=>''));
      i++;
    }
  }
  await browser?.close();process.exitCode=1
});

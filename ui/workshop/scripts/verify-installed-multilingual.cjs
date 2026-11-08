/* Production content adapter, real bilingual package and real local Jupyter. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const f=JSON.parse(fs.readFileSync(process.argv[2])),out=process.argv[3];
const checks=[],screenshots=[],errors=[];
let browser;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 browser=await chromium.launch({headless:true});
 async function login(which,locale){
  const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
  const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
  const base='http://127.0.0.1:'+f[which].port;
  await page.goto(base+'/login?token='+encodeURIComponent(f[which].token));
  await page.goto(base+'/workshop/?locale='+locale);
  await page.locator('html[lang="'+locale+'"]').waitFor();
  return {page,context,base};
 }
 async function shot(page,name){
  await page.evaluate(()=>document.fonts.ready);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,name+' overflow');
  await page.screenshot({path:path.join(out,name+'.png')});screenshots.push(name+'.png');
 }
 async function json(page,base,url){const r=await page.request.get(base+url);assert.equal(r.status(),200,url);return r.json()}
 async function poll(fn){for(let i=0;i<150;i++){if(await fn())return;await sleep(100)}throw Error('condition timed out')}
 async function execute(page,base,kernel,code){
  return page.evaluate(({base,kernel,code})=>new Promise((resolve,reject)=>{
   const id=crypto.randomUUID(),ws=new WebSocket(base.replace('http','ws')+'/api/kernels/'+kernel+'/channels');let text='';
   const timer=setTimeout(()=>{ws.close();reject(Error('kernel probe timeout'))},45000);
   ws.onopen=()=>ws.send(JSON.stringify({header:{msg_id:id,username:'review',session:crypto.randomUUID(),msg_type:'execute_request',version:'5.3'},parent_header:{},metadata:{},channel:'shell',content:{code,silent:false,store_history:false,user_expressions:{},allow_stdin:false,stop_on_error:true}}));
   ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.parent_header?.msg_id!==id)return;if(m.msg_type==='stream')text+=m.content.text;
    if(m.msg_type==='error'){clearTimeout(timer);ws.close();reject(Error(m.content.ename+': '+m.content.evalue))}
    if(m.msg_type==='status'&&m.content.execution_state==='idle'){clearTimeout(timer);ws.close();resolve(text)}};
  }),{base,kernel,code});
 }
 const a=await login('a','en'),b=await login('b','kk-Cyrl');
 assert.equal(await a.page.locator('.module').count(),1);assert.equal(await b.page.locator('.module').count(),1);
 await shot(a.page,'alice-en-outline');await shot(b.page,'bob-kk-outline');
 const course=f.a.plan.courses[0],module=course.modules[0],lab=course.course_dir+'/'+module.lab;
 const quiz=course.course_dir+'/'+module.quiz.replace(/\.yaml$/,'.ipynb');
 const lesson=course.course_dir+'/'+module.course;
 const guide=course.course_dir+'/README.md';
 await b.page.goto(b.base+'/workshop/content/'+guide+'?locale=kk-Cyrl#environment');
 await b.page.locator('#environment').waitFor({state:'attached'});
 assert.equal(await b.page.locator('#environment').evaluate(el=>Math.abs(el.getBoundingClientRect().top)<64),true);
 await b.page.locator('#workshop-language').click();await b.page.locator('.language-menu a[data-locale="en"]').click();
 await b.page.locator('html[lang="en"]').waitFor();assert.equal(new URL(b.page.url()).hash,'#environment');
 await b.page.goto(b.base+'/workshop/content/'+lesson+'?locale=kk-Cyrl');
 assert.equal(await b.page.locator('article img,.lesson img,main img').count()>=6,true);
 for(const img of await b.page.locator('main img').all())assert.equal(await img.evaluate(el=>el.complete&&el.naturalWidth>0),true);
 await shot(b.page,'bob-kk-lesson');
 const next=await b.page.request.get(b.base+'/workshop/content/'+lesson.replace('module01-introduction','module02-architecture')+'?locale=kk-Cyrl');
 assert.equal(new URL(next.url()).searchParams.get('locale'),'en');
 assert.equal((await b.page.request.get(b.base+'/workshop/notebook/'+lab.replace('lab01_start_doris_and_build_baseline','optional01_metabase_dashboard')+'?locale=kk-Cyrl')).status(),406);
 assert.equal((await b.page.request.get(b.base+'/workshop/?locale=fr')).status(),406);
 checks.push('one-module closure, source fragments, six images, explicit English mirror, unavailable optional/language');
 async function open(which,notebook,locale,fragment=''){
  const manager=await which.context.newPage();await manager.goto(which.base+'/workshop/notebook/'+notebook+'?locale='+locale+fragment);
  const messages=f.messages[locale];await manager.getByRole('heading',{name:messages['notebook.title'],exact:true}).waitFor();
  const openLink=manager.getByRole('link',{name:messages['notebook.open'],exact:true});
  if(fragment)assert.equal(new URL(await openLink.getAttribute('href'),manager.url()).hash,fragment);
  const promise=which.context.waitForEvent('page');await openLink.click();
  const page=await promise;await page.locator('.jp-Notebook').waitFor({timeout:60000});
  const news=page.getByRole('button',{name:'No',exact:true});if(await news.isVisible())await news.click();
  await page.getByRole('menuitem',{name:'View',exact:true}).click();
  await page.getByRole('menuitem',{name:/Expand All Code/}).click();
  await page.locator('.jp-CodeCell .cm-content').first().waitFor();
  return {page,manager,work:'work/'+locale+'/'+notebook};
 }
 const en=await open(a,lab,'en');
 const anchors=en.page.locator('[id^="unit-u."],[data-jupyter-id^="unit-u."]');
 assert.equal(await anchors.count()>0,true,'stable Notebook anchors');
 const fragment=await anchors.first().evaluate(el=>el.id||el.getAttribute('data-jupyter-id'));
 // Jupyter virtualizes cells, so the last mounted editor is not necessarily
 // the last Notebook cell. Insert through its toolbar and edit the active cell.
 const beforeEdit=(await json(a.page,a.base,'/api/contents/'+en.work)).content;
 await en.page.locator('.jp-CodeCell .cm-content').first().click();await en.page.keyboard.press('Escape');
 await en.page.getByRole('button',{name:'Insert a cell below (B)',exact:true}).click();
 const editor=en.page.locator('.jp-Notebook .jp-Cell.jp-mod-active .cm-content');await editor.click();
 assert.equal(await editor.textContent(),'');
 await en.page.keyboard.insertText("ml05_sentinel = 731\nprint('ML05_SAVED_OUTPUT', ml05_sentinel)");
 await en.page.keyboard.press('Shift+Enter');await en.page.getByText('ML05_SAVED_OUTPUT 731',{exact:true}).waitFor({timeout:60000});
 await en.page.keyboard.press(process.platform==='darwin'?'Meta+s':'Control+s');
 await poll(async()=>JSON.stringify((await json(a.page,a.base,'/api/contents/'+en.work)).content).includes('ML05_SAVED_OUTPUT'));
 const saved=(await json(a.page,a.base,'/api/contents/'+en.work)).content;
 for(const cell of beforeEdit.cells){const after=saved.cells.find(c=>c.id===cell.id);assert.ok(after);assert.equal(after.cell_type,cell.cell_type);assert.deepEqual(after.source,cell.source);}
 const session=(await json(a.page,a.base,'/api/sessions')).find(s=>s.path===en.work);assert.ok(session);
 assert.equal((await execute(en.page,a.base,session.kernel.id,'print(ml05_sentinel)')).trim(),'731');
 await shot(en.page,'alice-en-edited-notebook');
 const kk=await open(a,lab,'kk-Cyrl');
 const sessions=await json(a.page,a.base,'/api/sessions');assert.ok(sessions.find(s=>s.path===en.work&&s.kernel.id===session.kernel.id));
 assert.notEqual(sessions.find(s=>s.path===kk.work).kernel.id,session.kernel.id);
 const probe=await execute(kk.page,a.base,sessions.find(s=>s.path===kk.work).kernel.id,"import json,pathlib,doris_course,doris_labkit\nprint(json.dumps({'cwd':str(pathlib.Path.cwd()),'course':doris_course.__file__,'common':doris_labkit.__file__,'sentinel':globals().get('ml05_sentinel')}))");
 const state=JSON.parse(probe.trim());assert.equal(state.cwd,path.join(fs.realpathSync(f.a.source),course.course_dir));assert.equal(state.sentinel,null);
 assert.equal(state.course.startsWith(fs.realpathSync(f.a.source)),true);assert.equal(state.common.startsWith(fs.realpathSync(f.a.source)),true);
 const again=await open(a,lab,'en','#'+fragment);assert.deepEqual((await json(a.page,a.base,'/api/contents/'+en.work)).content,saved);
 assert.equal((await json(a.page,a.base,'/api/sessions')).find(s=>s.path===en.work).kernel.id,session.kernel.id);
 assert.equal((await execute(en.page,a.base,session.kernel.id,'print(ml05_sentinel)')).trim(),'731');
 const downloadPromise=en.manager.waitForEvent('download');await en.manager.getByRole('link',{name:f.messages.en['notebook.download'],exact:true}).click();
 const downloaded=await downloadPromise;const filename=await downloaded.path();const downloadedNotebook=JSON.parse(fs.readFileSync(filename));
 assert.equal(JSON.stringify(downloadedNotebook).includes('ML05_SAVED_OUTPUT'),true);
 const outputCell=downloadedNotebook.cells.find(c=>[].concat(c.source).join('').includes('ml05_sentinel = 731'));
 assert.ok(outputCell);assert.equal(JSON.stringify(outputCell.outputs).includes('ML05_SAVED_OUTPUT 731'),true);
 assert.equal((await json(b.page,b.base,'/api/sessions')).length,0);
 checks.push('real UI edit/run/save/download, per-language resume, original kernel ID/memory/output retained, shared cwd/imports, separate kernel memory and learner isolation');
 const q=await open(b,quiz,'kk-Cyrl');
 await poll(async()=>(await json(b.page,b.base,'/api/kernels')).some(k=>k.execution_state==='idle'));
 await q.page.getByRole('menuitem',{name:'Run',exact:true}).click();
 await q.page.getByRole('menuitem',{name:'Run All Cells',exact:true}).click();
 await q.page.locator('.doris-quiz-question').waitFor({timeout:60000});
 const msg=f.messages['kk-Cyrl'];
 for(let i=0;i<f.correct['kk-Cyrl'].length;i++){
  await q.page.getByRole('radio',{name:f.correct['kk-Cyrl'][i],exact:true}).check();
  await q.page.getByRole('button',{name:new RegExp(msg['quiz.text-2'])}).click();
  const nextLabel=i===5?msg['quiz.text-3']:msg['quiz.text-4'];
  await q.page.getByRole('button',{name:new RegExp(nextLabel)}).click();
 }
 await q.page.getByText(msg['quiz.text-23'],{exact:false}).waitFor({timeout:30000});await shot(q.page,'bob-kk-real-quiz-saved');
 const progress=await json(b.page,b.base,'/workshop/progress/export');assert.equal(progress.attempts.length,1);assert.equal(progress.attempts[0].locale,'kk-Cyrl');assert.equal(progress.records[0].module_id,'module-1');assert.equal(progress.records[0].best_correct,6);
 const englishProgress=await json(b.page,b.base,'/workshop/progress/export?locale=en');assert.deepEqual(englishProgress.records,progress.records);assert.deepEqual(englishProgress.attempts,progress.attempts);
 checks.push('unchanged canonical Quiz code loads admitted Kazakh presentation and saves 6/6 to shared module identity');
 await q.page.keyboard.press('Escape');await q.page.keyboard.press(process.platform==='darwin'?'Meta+b':'Control+b');
 for(const width of [390,320]){
  await b.page.goto(b.base+'/workshop/notebook/'+lab+'?locale=kk-Cyrl');await b.page.setViewportSize({width,height:900});await shot(b.page,'kk-copy-'+width);
  await b.page.goto(b.base+'/workshop/content/'+lesson+'?locale=kk-Cyrl');await shot(b.page,'kk-lesson-'+width);
  await q.page.setViewportSize({width,height:900});await q.page.locator('.doris-quiz-root').evaluate(el=>el.scrollIntoView({block:'start'}));await shot(q.page,'kk-quiz-'+width);
 }
 assert.deepEqual(errors,[]);
 fs.writeFileSync(path.join(out,'report.json'),JSON.stringify({passed:true,package_digest:f.a.plan.content.package_digest,checks,screenshots,page_errors:errors},null,2));
 console.log(JSON.stringify({passed:true,checks:checks.length,screenshots:screenshots.length}));
 await browser.close();
})().catch(async error=>{fs.writeFileSync(path.join(out,'failure.json'),JSON.stringify({error:error.stack,checks,screenshots,errors},null,2));console.error(error.stack);if(browser){for(const [i,c] of browser.contexts().entries()){for(const [j,p] of c.pages().entries())await p.screenshot({path:path.join(out,'failure-'+i+'-'+j+'.png')}).catch(()=>{});}await browser.close();}process.exitCode=1});

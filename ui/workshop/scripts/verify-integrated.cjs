/* Actual local HTTP pages; fixture credentials stay in a private file and are never printed. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),http=require('http'),path=require('path');
const fixture=JSON.parse(fs.readFileSync(process.argv[2]));
const root=path.resolve(__dirname,'..'),out=path.join(root,'review/selected');
let subject='subject-alice',browser;
function forward(request,port,host,extra={}){
 return new Promise((resolve,reject)=>{
  const url=new URL(request.url()),headers={...request.headers(),host,'accept-encoding':'identity',...extra};
  delete headers['content-length'];
  const req=http.request({hostname:'127.0.0.1',port,path:url.pathname+url.search,method:request.method(),headers},response=>{
   const chunks=[];response.on('data',b=>chunks.push(b));response.on('end',()=>{
    const h={...response.headers};for(const name of ['connection','transfer-encoding','content-length','set-cookie'])delete h[name];
    resolve({status:response.statusCode,headers:h,body:Buffer.concat(chunks)});
   });
  });req.on('error',reject);const data=request.postDataBuffer();if(data)req.write(data);req.end();
 });
}
(async()=>{
 browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_EXECUTABLE}:{})});
 const context=await browser.newContext(),issues=[],errors=[],screenshots=[];
 await context.route('https://**/*',async route=>{
  const request=route.request(),host=new URL(request.url()).hostname;
  if(host==='workshops.example.com'||host==='class-a-entry.workshops.example.com'){
   const result=await forward(request,fixture.portal_port,host,host.includes('-entry.')?{'x-amzn-oidc-data':fixture.oidc[subject]}:{});
   await route.fulfill(result);
  }else if(host==='class-a-alice-jupyter.workshops.example.com'){
   await route.fulfill(await forward(request,fixture.course_port,'127.0.0.1',{'authorization':'token '+fixture.jupyter_token}));
  }else{await route.fulfill({status:403,body:'Fixture route unavailable'});}
 });
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 page.on('console',m=>{if(m.type()==='error'&&/Content Security Policy|Refused to/i.test(m.text()))errors.push(m.text())});
 const apex='https://workshops.example.com/doris-courses/',entry='https://class-a-entry.workshops.example.com/doris-courses/';
 async function snapshot(name){
  await page.evaluate(()=>document.fonts.ready);
  const result=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth,h1:document.querySelectorAll('h1').length,missing:[...document.images].filter(i=>!i.complete||!i.naturalWidth).map(i=>i.alt),scripts:document.querySelectorAll('script').length,font:document.fonts.check('16px Manrope'),brand:getComputedStyle(document.querySelector('.course-board')||document.body).backgroundColor}));
  if(result.overflow||result.h1!==1||result.missing.length||result.scripts!==1||!result.font)issues.push({name,...result});
  const file='integrated-'+name+'.png';await page.screenshot({path:path.join(out,file),fullPage:false});screenshots.push(file);
 }
 await page.goto(apex);await page.getByRole('heading',{name:'Workshops',exact:true}).waitFor();
 for(const width of [1440,390,320]){await page.setViewportSize({width,height:1000});await snapshot('activities-'+width)}
 await page.goto(entry);await page.getByRole('heading',{name:'My workspace',exact:true}).waitFor();
 const body=await page.locator('main').innerText();if(body.includes('bob')||body.includes('demo-rta')||body.includes('s001'))issues.push({reason:'sample or other identity rendered'});
 const tools=await page.locator('.tool-row h3').allTextContents();if(tools.length!==3||!tools.includes('Doris · single FE'))issues.push({reason:'real service IDs did not map to assigned tools'});
 for(const width of [1440,390,320]){await page.setViewportSize({width,height:1000});await snapshot('workspace-'+width)}
 await page.setViewportSize({width:1440,height:1000});await page.getByRole('link',{name:'Open course',exact:true}).click();await page.locator('.module').first().waitFor();
 if(await page.locator('.module').count()!==10)issues.push({reason:'course module count'});
 for(const width of [1440,390,320]){await page.setViewportSize({width,height:1000});await snapshot('course-'+width)}
 await page.getByRole('link',{name:'Read lesson',exact:false}).first().click();await page.locator('.article').waitFor();
 if(!await page.locator('.article').innerText().then(x=>x.includes('10,158,080')))issues.push({reason:'real lesson body missing'});
 for(const width of [1440,390,320]){await page.setViewportSize({width,height:1000});await snapshot('lesson-'+width)}
 const lab=page.locator('.reading-side a.primary');if(!await lab.getAttribute('href').then(x=>x.includes('/lab/tree/doris-course/01-real-time-analytics/')))issues.push({reason:'Lab destination'});
 if(await lab.getAttribute('rel')!=='noopener')issues.push({reason:'Lab opener protection'});
 await page.getByRole('link',{name:'Web Terminal',exact:true}).click();await page.locator('.steps').waitFor();await snapshot('terminal');
 await page.goto('https://class-a-entry.workshops.example.com/_workshop/logged-out');await page.getByRole('heading',{name:'Signed out of this entry',exact:true}).waitFor();await snapshot('signed-out');
 subject='unassigned';const response=await page.goto(entry);if(response.status()!==403)issues.push({reason:'unassigned account status'});await snapshot('denied');
 subject='subject-bob';await page.goto(entry);const bob=await page.locator('main').innerText();if(bob.includes('alice'))issues.push({reason:'other assignment visible'});
 const report={ok:!issues.length&&!errors.length,scope:'Real loopback portal and Jupyter handlers; fixture-signed OIDC and local Jupyter token, no real ALB/Cognito or AWS resources',screenshots,issues,errors,checked_at:new Date().toISOString()};
 fs.writeFileSync(path.join(out,'integrated-verification.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({...report,screenshots:screenshots.length},null,2));
 await browser.close();if(!report.ok)process.exitCode=1;
})().catch(async e=>{console.error(e.message);await browser?.close();process.exitCode=1});

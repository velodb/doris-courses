/* Real installed renderer and progress endpoints; no kernel execution or AWS. */
'use strict';
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const fixture=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const out=path.resolve(process.argv[3]);fs.mkdirSync(out,{recursive:true});
const base='http://127.0.0.1:'+fixture.port;
const report={passed:false,checks:[],screenshots:[],pageErrors:[]};
const hash=i=>'#workshop-course-'+i;
async function main(){
 const browser=await chromium.launch({channel:'chrome'});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
  const page=await context.newPage();page.on('pageerror',e=>report.pageErrors.push(e.message));
  await page.goto(base+'/login?token='+encodeURIComponent(fixture.token));
  const before=await (await context.request.get(base+'/workshop/progress/export')).json();
  const preserve=async()=>page.evaluate(raw=>{
   const original=new DOMParser().parseFromString(raw,'text/html');
   // Tornado generates a fresh mask for the same XSRF cookie on each GET.
   const forms=doc=>[...doc.querySelectorAll('.course-outline form')].map(form=>{
    const copy=form.cloneNode(true);copy.querySelectorAll('input[name="_xsrf"]').forEach(input=>input.setAttribute('value',input.value?'[present]':'[missing]'));return copy.outerHTML;
   });
   const links=doc=>[...doc.querySelectorAll('.course-outline a[href]')].map(a=>a.getAttribute('href'));
   return {forms:JSON.stringify(forms(original))===JSON.stringify(forms(document)),links:JSON.stringify(links(original))===JSON.stringify(links(document)),modules:document.querySelectorAll('.module').length};
  },await (await context.request.get(page.url())).text());
  const visibleHeading=async(i)=>{
   const target=page.locator('#workshop-course-'+i+' > .section-heading h2');
   const result=await target.evaluate(node=>{const nav=document.querySelector('.topnav'),side=document.querySelector('.course-sidebar');return {y:node.getBoundingClientRect().top,cover:Math.max(nav.getBoundingClientRect().bottom,innerWidth<=700?side.getBoundingClientRect().bottom:0)}});
   assert.ok(result.y>=result.cover-2&&result.y<1000,JSON.stringify(result));
  };
  for(const locale of ['en','kk-Cyrl']){
   await page.setViewportSize({width:1440,height:1000});
   await page.goto(base+'/workshop/?locale='+locale);
   await page.locator('.course-switcher-link').last().waitFor();await page.evaluate(()=>document.fonts.ready);
   assert.equal(await page.locator('.course-switcher-link').count(),3);
   assert.equal(await page.locator('.module').count(),32);
   assert.deepEqual(await preserve(),{forms:true,links:true,modules:32});
   for(const [i,levels] of [[2,3],[3,2],[1,3]]){
    await page.locator('.course-switcher-link[href="'+hash(i)+'"]').click();await page.waitForTimeout(150);
    assert.equal(await page.locator('.course-switcher-link[aria-current]').getAttribute('href'),hash(i));
    assert.equal(await page.locator('.course-level-group:not([hidden]) a').count(),levels);
    await visibleHeading(i);
   }
   await page.locator('.course-switcher-link[href="'+hash(2)+'"]').click();await page.waitForTimeout(100);
   await page.goBack();await page.waitForTimeout(150);assert.ok(page.url().endsWith(hash(1)));
   await page.goForward();await page.waitForTimeout(150);assert.ok(page.url().endsWith(hash(2)));await visibleHeading(2);
   await page.locator('.course-level-group:not([hidden]) a').last().click();await page.waitForTimeout(150);
   assert.ok(page.url().endsWith('workshop-course-2-level-3'));
   await page.reload();await page.locator('.course-switcher-link').last().waitFor();await page.waitForTimeout(200);
   assert.equal(await page.locator('.course-switcher-link[aria-current]').getAttribute('href'),hash(2));
   assert.ok(page.url().endsWith('workshop-course-2-level-3'));
   await page.locator('.course-progress-tools > summary').click();
   await page.locator('.course-progress-tools a[href*="/progress/export"]').waitFor();
   await page.locator('.course-progress-tools > summary').press('Escape');assert.equal(await page.locator('.course-progress-tools').getAttribute('open'),null);
   report.checks.push(locale+': all three courses/32 modules, forms and URLs intact; jump focus, level navigation, history, deep-link reload and progress tools passed');
   for(const width of [1440,768,390,320]){
    await page.setViewportSize({width,height:1000});await page.goto(base+'/workshop/?locale='+locale);await page.locator('.course-switcher-link').last().waitFor();await page.evaluate(()=>document.fonts.ready);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,locale+' '+width);
    for(const link of await page.locator('.course-switcher-link').all()){const rect=await link.boundingBox();assert.ok(rect&&rect.x>=0&&rect.x+rect.width<=width+1&&rect.y+rect.height<1000);}
    await page.locator('.course-switcher-link[href="'+hash(3)+'"]').click();await page.waitForTimeout(150);await visibleHeading(3);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    const name='installed-'+locale+'-'+width+'.png';await page.screenshot({path:path.join(out,name),fullPage:false});report.screenshots.push(name);
   }
   report.checks.push(locale+': 1440/768/390/320px actual installed pages show every course, jump below fixed bars and have no horizontal overflow');
  }
  await page.setViewportSize({width:1440,height:1000});await page.goto(base+'/workshop/?locale=en'+hash(2));await page.locator('.course-switcher-link').last().waitFor();await page.waitForTimeout(200);
  await page.locator('summary.language-trigger').click();await page.locator('[data-locale="kk-Cyrl"]').click();await page.waitForURL(/locale=kk-Cyrl/);await page.locator('.course-switcher-link').last().waitFor();await page.waitForTimeout(200);
  assert.ok(page.url().endsWith(hash(2)),page.url());assert.equal(await page.locator('.course-switcher-link[aria-current]').getAttribute('href'),hash(2));await visibleHeading(2);
  const after=await (await context.request.get(base+'/workshop/progress/export')).json();assert.deepEqual(after.records,before.records);assert.deepEqual(after.attempts,before.attempts);
  const plain=await browser.newContext({javaScriptEnabled:false});const fallback=await plain.newPage();await fallback.goto(base+'/login?token='+encodeURIComponent(fixture.token));await fallback.goto(base+'/workshop/?locale=en');assert.equal(await fallback.locator('.course-outline').count(),3);assert.equal(await fallback.locator('.module').count(),32);await plain.close();
  report.checks.push('Actual language picker retains selected course; progress/attempt records unchanged; no-JavaScript outline remains complete');
  assert.deepEqual(report.pageErrors,[]);report.passed=true;
 }finally{await browser.close();fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2)+'\n');}
 console.log(JSON.stringify({passed:report.passed,checks:report.checks.length,screenshots:report.screenshots.length,pageErrors:report.pageErrors.length}));
}
main().catch(e=>{console.error(e.stack);process.exitCode=1});

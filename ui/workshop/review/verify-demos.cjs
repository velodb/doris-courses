/* Verify the offline prototype paths and layout. Requires an existing Playwright installation. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('fs');
const path=require('path');
const {pathToFileURL}=require('url');
const root=__dirname;
const variants=['a-lab-manual','b-tool-workspace','c-reading-first'];
const views=['workspace','activity','sign-in','first-login','mfa','login-error','course','lesson','terminal','external','no-activity','ended','not-ready','denied','expired','signed-out','help'];
let runningBrowser;
(async()=>{
 const browser=runningBrowser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_EXECUTABLE}:{})});
 const report={checked_at:new Date().toISOString(),variants:[],issues:[]};
 for(const name of variants){
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  const page=await context.newPage();
  const errors=[],external=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(!r.url().startsWith('file:')&&!r.url().startsWith('data:'))external.push(r.url())});
  const url=pathToFileURL(path.join(root,'design-demos',name+'.html')).href;
  await page.goto(url);await page.evaluate(()=>document.fonts.ready);
  const screenshots=[]; const overflows=[];const contrast=[];
  for(const size of [{width:1440,height:1000},{width:768,height:1024},{width:390,height:844}]){
   await page.setViewportSize(size);
   for(const view of views){
    await page.selectOption('#view-picker',view);
    await page.evaluate(()=>document.fonts.ready);
    const layout=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,missing:[...document.images].filter(x=>!x.complete||!x.naturalWidth).map(x=>x.alt),h1:document.querySelector('h1')?.textContent,fields:[...document.querySelectorAll('input')].every(x=>x.readOnly)}));
    if(layout.scroll>layout.width+1||layout.missing.length||!layout.h1||!layout.fields)overflows.push({size,view,...layout});
    if(size.width===1440){
     const failures=await page.evaluate(()=>{
      const rgba=s=>s.match(/[\d.]+/g).map(Number),lum=c=>c.slice(0,3).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((n,v,i)=>n+v*[.2126,.7152,.0722][i],0);
      const issues=[];
      for(const el of document.querySelectorAll('#main *')){
       if(el.closest('[aria-hidden="true"]')||![...el.childNodes].some(n=>n.nodeType===3&&n.textContent.trim())||!el.getClientRects().length)continue;
       const cs=getComputedStyle(el);let parent=el,bg=[255,255,255,1];
       while(parent){const c=rgba(getComputedStyle(parent).backgroundColor);if(c.length===3||c[3]===1){bg=c;break}parent=parent.parentElement}
       const fg=rgba(cs.color);const a=lum(fg),b=lum(bg);const ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);const large=parseFloat(cs.fontSize)>=24||parseFloat(cs.fontSize)>=18.66&&Number(cs.fontWeight)>=700;
       if(ratio<(large?3:4.5)-.01)issues.push({text:el.textContent.trim().slice(0,60),ratio:Math.round(ratio*100)/100,fg:cs.color,bg,font:cs.fontSize});
      }return issues;
     });if(failures.length)contrast.push({view,failures});
    }
    if(['workspace','activity','sign-in','lesson'].includes(view)){
     const file=`${name}-${view}-${size.width}.png`;
     await page.screenshot({path:path.join(root,'screenshots',file),fullPage:view==='lesson'});
     screenshots.push(file);
    }
   }
  }
  await page.setViewportSize({width:1440,height:1000});
  await page.selectOption('#view-picker','activity');
  await page.getByRole('button',{name:'Sign in to workshop'}).click();
  if(await page.locator('h1').innerText()!=='Sign in')throw Error('Activity to sign-in failed');
  await page.getByRole('button',{name:'Sign in',exact:true}).click();
  if(await page.locator('h1').innerText()!=='Change password')throw Error('First login failed');
  await page.getByRole('button',{name:'Change password',exact:true}).click();
  await page.locator('main [data-view="course"]').first().click();
  if(await page.locator('.module').count()!==10)throw Error('Course module count mismatch');
  await page.locator('[data-view="lesson"][data-module="0"]').click();
  await page.getByRole('button',{name:'Open Lab 1',exact:true}).first().click();
  if(!await page.locator('h1').innerText().then(x=>x.includes('Lab 1')))throw Error('Lab boundary failed');
  await page.selectOption('#view-picker','workspace');
  await page.getByRole('button',{name:'Sign out of this entry',exact:true}).click();
  if(await page.locator('h1').innerText()!=='Signed out of this entry')throw Error('Signout failed');
  await page.getByRole('button',{name:'Back to activities',exact:true}).click();
  await page.selectOption('#view-picker','workspace');
  await page.getByRole('button',{name:'Web Terminal',exact:true}).first().click();
  if(!await page.locator('h1').innerText().then(x=>x.includes('terminal')))throw Error('Terminal failed');
  await page.selectOption('#view-picker','workspace');
  await page.keyboard.press('Tab');
  const keyboard=await page.evaluate(()=>document.activeElement.matches('button,a,input,select'));
  const r={name,screenshots,view_checks:views.length*3,errors,external_requests:external,overflows,contrast_failures:contrast,keyboard_focus:keyboard,flows:'activity → sign-in → first password → workspace → outline → lesson → Lab boundary; sign-out → activities; terminal guide'};
  report.variants.push(r);if(errors.length||external.length||overflows.length||contrast.length||!keyboard)report.issues.push(name);
  await context.close();
 }
 await browser.close();
 report.ok=report.issues.length===0;
 fs.writeFileSync(path.join(root,'verification.json'),JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify({ok:report.ok,view_checks:report.variants.reduce((n,x)=>n+x.view_checks,0),issues:report.issues,details:report.variants.map(x=>({name:x.name,errors:x.errors,external_requests:x.external_requests,overflows:x.overflows,contrast_failures:x.contrast_failures,keyboard_focus:x.keyboard_focus}))},null,2));
 if(!report.ok)process.exitCode=1;
})().catch(async e=>{console.error(e);await runningBrowser?.close();process.exitCode=1});

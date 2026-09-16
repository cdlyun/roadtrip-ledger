// Run against an isolated, seeded local database only. Never use production.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawn } = require('node:child_process');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const artifacts = process.env.TEST_ARTIFACTS || fs.mkdtempSync(path.join(os.tmpdir(),'roadtrip-browser-'));
const repo = path.resolve(__dirname, '..');
let server;
async function bounded(promise,label){
  let timer;
  try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(`${label} not observed within 10s`)),10000);})]);}
  finally{clearTimeout(timer);}
}
async function startIsolatedServer(){
  // Always create our own database and OS-assigned loopback port. Never seed an
  // arbitrary pre-existing localhost service or expose the fixture on the LAN.
  const dbDir=fs.mkdtempSync(path.join(os.tmpdir(),'roadtrip-db-'));
  const python='import app; app.init_db(); server=app.ThreadingHTTPServer(("127.0.0.1",0),app.Handler); print("TEST_BASE_URL=http://127.0.0.1:"+str(server.server_port),flush=True); server.serve_forever()';
  server=spawn(process.env.PYTHON||'python3',['-u','-c',python],{cwd:repo,env:{...process.env,ROADTRIP_DB:path.join(dbDir,'test.db'),PYTHONDONTWRITEBYTECODE:'1'},stdio:['ignore','pipe','pipe']});
  let output=''; server.stdout.on('data',b=>output+=b); server.stderr.on('data',b=>output+=b);
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{server.kill('SIGTERM');reject(new Error(`isolated server did not start: ${output}`));},10000);
    server.once('error',e=>{clearTimeout(timer);reject(e);});
    server.once('exit',code=>{clearTimeout(timer);reject(new Error(`fixture exited ${code}: ${output}`));});
    server.stdout.on('data',()=>{const match=output.match(/TEST_BASE_URL=(http:\/\/127\.0\.0\.1:\d+)/);if(match){clearTimeout(timer);resolve(match[1]);}});
  });
}
async function seed(request,base){
  const localNow=(offset=0)=>{const d=new Date(Date.now()+offset);const p=n=>String(n).padStart(2,'0');return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;};
  const tripResponse=await request.post(`${base}/api/trips`,{data:{name:'浏览器隔离回归',origin:'成都',destination:'兰州',start_odometer:32000,planned_days:5}}); assert.equal(tripResponse.ok(),true); const trip=await tripResponse.json();
  const entryResponse=await request.post(`${base}/api/entries`,{data:{trip_id:trip.id,raw_text:'隔离种子长标题消费',recognized:{category:'meal',amount:123456.78,location:'一个非常长的测试地点名称用于窄卡宽度检查',occurred_at:localNow(),item:'一个很长很长的消费标题用于检查文本溢出和操作区布局'},client_id:'seed-entry-v3.1.3',client_revision:1}}); if(!entryResponse.ok()) throw new Error(`seed entry failed ${entryResponse.status()} ${await entryResponse.text()}`);
  for(const [odometer,location,offset] of [[32100,'成都',-120000],[32200,'广元',-60000]]) { const response=await request.post(`${base}/api/odometer-readings`,{data:{trip_id:trip.id,odometer,location,occurred_at:localNow(offset),client_id:`seed-odo-${odometer}`,client_revision:1}}); if(!response.ok()) throw new Error(`seed odometer failed ${response.status()} ${await response.text()}`); }
  assert.equal((await (await request.get(`${base}/api/odometer-readings?trip_id=${trip.id}`)).json()).length,2);
  const dashboard=await (await request.get(`${base}/api/dashboard?trip_id=${trip.id}`)).json();
  assert.equal(dashboard.entries.length,1,'seed must contain its long-content entry');
}
(async () => {
  let browser;
  try {
    const base=await startIsolatedServer();
    browser = await chromium.launch({headless:true, ...(process.env.CHROMIUM_EXECUTABLE ? {executablePath:process.env.CHROMIUM_EXECUTABLE} : {})});
    const context = await browser.newContext({serviceWorkers:'block'});
    const page = await context.newPage();
    await seed(context.request,base);
    const errors = [];
    const layoutFailures = [];
    page.on('pageerror', e => errors.push(e.message));
    for (const width of [344, 412, 647, 648, 710, 711, 720, 840, 991, 992]) {
      await page.setViewportSize({width,height:width===711 ? 584 : width===344 ? 683 : 776});
      await page.goto(base);
      await page.locator('#trip-active:not(.hidden)').waitFor();
      for (const screen of ['home','trip','bills','stats']) {
        await page.locator(`.bottom-nav [data-go-screen="${screen}"]`).click();
        await page.waitForTimeout(350); // Let CSS screen/sheet transitions settle before geometry and screenshots.
        const geometry = await page.locator(`.app-screen[data-screen="${screen}"]`).evaluate(el => ({
          scroll: document.documentElement.scrollWidth,
          viewport: innerWidth,
          columns: getComputedStyle(el).gridTemplateColumns,
          targets: [...el.querySelectorAll('button,a,summary')].filter(n => n.getClientRects().length).map(n => ({text:n.textContent.trim(),width:n.getBoundingClientRect().width,height:n.getBoundingClientRect().height}))
        }));
        if (geometry.scroll > width + 1) layoutFailures.push(`${width} ${screen}: horizontal overflow ${JSON.stringify(geometry)}`);
        const small = geometry.targets.filter(t => t.height < 47.9 || t.width < 47.9);
        if (small.length) layoutFailures.push(`${width} ${screen}: undersized targets ${JSON.stringify(small)}`);
        const rail=await page.locator('.bottom-nav').evaluate(el=>({width:el.getBoundingClientRect().width,position:getComputedStyle(el).position}));
        if(width >= 648 && width < 992){ assert.ok(Math.abs(rail.width-88)<2,`${width}: expected 88px rail, got ${rail.width}`); assert.equal(geometry.columns.split(' ').length,2,`${width} ${screen}: not two columns`); }
        if(width >= 648 && screen==='bills') {
          await page.locator('#history-card:not(.hidden)').waitFor({timeout:5000});
          await page.waitForFunction(()=>document.querySelectorAll('#history .history-item').length>0,null,{timeout:5000});
          const cards = await page.locator('#history-card,#trash-card').evaluateAll(nodes=>nodes.filter(n=>n.getClientRects().length).map(n=>({x:n.getBoundingClientRect().x,y:n.getBoundingClientRect().y,width:n.getBoundingClientRect().width})));
          if(cards.length>1) assert.ok(cards[1].x >= cards[0].x+cards[0].width, 'wide bills must actually place cards side by side');
          const textWidth = await page.locator('#history .history-item>div').first().evaluate(n=>n.getBoundingClientRect().width);
          assert.ok(textWidth >= 180,'bill description must remain readable, not squeezed by actions');
        }
        if(width === 344 || width === 711 || width === 992) await page.screenshot({path:path.join(artifacts,`${width}-${screen}.png`),fullPage:true});
      }
      await page.locator('[data-open-odometer]').click();
      await page.locator('#odometer-sheet.open').waitFor();
      assert.equal(await page.locator('body').evaluate(el=>el.style.overflow),'hidden');
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('body').evaluate(el=>el.style.overflow),'');
      assert.equal(await page.locator('[data-open-odometer]').evaluate(el=>el===document.activeElement),true);
    }
    await page.setViewportSize({width:344,height:683});
    await page.locator('#open-record-sheet').click();
    await page.locator('#record-sheet').evaluate(el=>el.querySelector('.sheet-close').focus());
    await page.keyboard.press('Shift+Tab');
    assert.equal(await page.locator('#parse-entry').evaluate(el=>el===document.activeElement),true,'Shift+Tab wraps to last visible control');
    await page.keyboard.press('Tab');
    assert.equal(await page.locator('#record-sheet .sheet-close').evaluate(el=>el===document.activeElement),true,'Tab wraps to first visible control');
    assert.equal(await page.locator('#odometer-sheet').evaluate(el=>el.inert),true,'closed sheet is inert');
    await page.locator('#entry-text').fill('在广元午餐吃米粉 30 元');
    await page.locator('#parse-entry').click();
    await page.waitForFunction(()=>document.querySelector('#entry-form [name=amount]').value==='30');
    assert.equal(await page.locator('#entry-form [name=category]').inputValue(),'meal');
    assert.match(await page.locator('#entry-form [name=item]').inputValue(),/米粉/);
    assert.doesNotMatch(await page.locator('#field-meta').innerText(),/category_label|people|nights|note|油号|升数|里程|挂牌|加满/);
    assert.match(await page.locator('#field-meta').innerText(),/记录时间：默认值/);
    // At short heights, closing remains sticky and clickable even at the sheet bottom.
    for(const height of [400,360]){
      await page.setViewportSize({width:344,height});
      await page.waitForFunction(()=>Math.abs(document.querySelector('#record-sheet').getBoundingClientRect().bottom-innerHeight)<1);
      await page.locator('#record-sheet').evaluate(el=>{el.scrollTop=el.scrollHeight;});
      const closeBox=await page.locator('#record-sheet .sheet-close').boundingBox();
      assert.ok(closeBox && closeBox.y>=0 && closeBox.y+closeBox.height<=height,`${height}: close button left viewport ${JSON.stringify(closeBox)}`);
      await page.locator('#record-sheet .sheet-close').click();
      await page.locator('#record-sheet:not(.open)').waitFor();
      await page.locator('#open-record-sheet').click();
      assert.equal(await page.locator('#entry-form [name=amount]').inputValue(),'30',`${height}: close/reopen lost draft`);
    }
    // Hand-edited fields must survive source editing, failed re-recognition, cancel, and reopen.
    await page.locator('#entry-form [name=amount]').fill('31');
    await page.locator('#entry-form [name=location]').fill('手动地点');
    await page.locator('#edit-source-text').click();
    await page.locator('#entry-card').waitFor();
    assert.equal(await page.locator('#entry-text').inputValue(),'在广元午餐吃米粉 30 元');
    let failedRequests=0;
    await page.route('**/api/parse',route=>{failedRequests++;return route.abort('failed');});
    page.once('dialog',dialog=>dialog.dismiss());
    await page.locator('#parse-entry').click();
    assert.equal(failedRequests,0,'dismissed confirmation must not issue a request');
    page.once('dialog',dialog=>dialog.accept());
    await page.locator('#parse-entry').click();
    await page.waitForFunction(()=>!document.querySelector('#parse-entry').disabled);
    assert.equal(failedRequests,1,'failure test must actually attempt the parse request');
    assert.equal(await page.locator('#entry-card').isVisible(),true,'failed re-recognition must keep source editor');
    await page.unroute('**/api/parse');
    await page.locator('#return-to-review').click(); await page.locator('#review-card').waitFor();
    assert.equal(await page.locator('#entry-form [name=amount]').inputValue(),'31','manual amount lost after source cancel');
    assert.equal(await page.locator('#entry-form [name=location]').inputValue(),'手动地点','manual location lost after source cancel');
    await page.locator('#record-sheet .sheet-close').click(); await page.locator('#open-record-sheet').click(); await page.locator('#review-card').waitFor();
    assert.equal(await page.locator('#entry-form [name=amount]').inputValue(),'31','manual amount lost after reopen');
    assert.equal(await page.locator('#entry-form [name=location]').inputValue(),'手动地点','manual location lost after reopen');
    await page.setViewportSize({width:711,height:584});
    assert.equal(await page.locator('#entry-form [name=amount]').inputValue(),'31');
    await page.setViewportSize({width:344,height:683});
    assert.equal(await page.locator('#entry-form [name=amount]').inputValue(),'31');
    const originalClientId=await page.evaluate(()=>state.draftClientId);
    const resumeAfterReload=async()=>{
      await page.reload();
      await page.locator('#trip-active:not(.hidden)').waitFor();
      await page.locator('#open-record-sheet').click();
      await page.locator('#draft-notice button').filter({hasText:'继续填写'}).click();
    };
    await page.locator('#edit-source-text').click();
    await page.locator('#entry-text').fill('在兰州午餐吃牛肉面 44 元');
    await page.waitForFunction(()=>currentDraft()?.source_edit?.active && currentDraft().raw_text==='在兰州午餐吃牛肉面 44 元');
    await resumeAfterReload();
    assert.equal(await page.locator('#entry-card').isVisible(),true,'reload resumes source edit, not mismatched confirmation');
    assert.equal(await page.locator('#entry-text').inputValue(),'在兰州午餐吃牛肉面 44 元');
    await page.locator('#return-to-review').click();
    assert.equal(await page.locator('#entry-form [name=amount]').inputValue(),'31');
    assert.equal(await page.locator('#entry-form [name=location]').inputValue(),'手动地点');
    assert.equal(await page.locator('#entry-text').inputValue(),'在广元午餐吃米粉 30 元');
    // A request for the same original text must still be invalidated on return.
    let releaseParse;
    const interceptedParse=new Promise(resolve=>releaseParse=resolve);
    await page.route('**/api/parse',route=>releaseParse(route));
    await page.locator('#edit-source-text').click();
    page.once('dialog',dialog=>dialog.accept());
    await page.locator('#parse-entry').click();
    const pendingRoute=await bounded(interceptedParse,'delayed parse request');
    await page.locator('#return-to-review').click();
    const lateResponse=page.waitForResponse(r=>r.url().endsWith('/api/parse'));
    await pendingRoute.fulfill({status:200,contentType:'application/json',body:JSON.stringify({raw_text:'在广元午餐吃米粉 30 元',recognized:{category:'meal',amount:999,location:'不应覆盖',item:'迟到结果'},missing:[],field_meta:{}})});
    await lateResponse;
    await page.waitForTimeout(100);
    assert.equal(await page.locator('#entry-form [name=amount]').inputValue(),'31','late response overwrote retained manual values');
    await page.unroute('**/api/parse');
    await page.locator('#edit-source-text').click();
    await page.locator('#entry-text').fill('在兰州午餐吃牛肉面 44 元');
    page.once('dialog',dialog=>dialog.accept());
    await page.locator('#parse-entry').click();
    await page.waitForFunction(()=>currentDraft()?.fields?.amount===44 && !currentDraft()?.source_edit);
    await resumeAfterReload();
    assert.equal(await page.locator('#entry-form [name=amount]').inputValue(),'44','successful reparse lost after immediate reload');
    assert.equal(await page.locator('#entry-form [name=location]').inputValue(),'兰州');
    assert.equal(await page.evaluate(()=>state.draftClientId),originalClientId,'reparse changed stable client ID');
    // Hold a save request, then verify source editing cannot start mid-save.
    let releaseSave;
    const interceptedSave=new Promise(resolve=>releaseSave=resolve);
    await page.route('**/api/entries',route=>releaseSave(route));
    await page.locator('#save-entry').click();
    const savingRoute=await bounded(interceptedSave,'intercepted save request');
    assert.equal(await page.evaluate(()=>state.savingEntry),true);
    if(!await page.locator('#edit-source-text').isDisabled()) await page.locator('#edit-source-text').click();
    assert.equal(await page.locator('#review-card').isVisible(),true,'source editor opened during save');
    assert.equal(await page.locator('#entry-card').isVisible(),false);
    await savingRoute.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'隔离测试：未保存任何记录'})});
    await page.waitForFunction(()=>!state.savingEntry);
    await page.unroute('**/api/entries');
    await page.keyboard.press('Escape');
    await page.locator('.bottom-nav [data-go-screen="trip"]').click();
    const trips = await (await context.request.get(`${base}/api/trips`)).json();
    const tripId = trips.find(t=>t.status==='active').id;
    assert.equal((await (await context.request.get(`${base}/api/dashboard?trip_id=${tripId}`)).json()).entries.length,1,'fold/source-edit/save interception created an entry');
    const readings = async () => (await context.request.get(`${base}/api/odometer-readings?trip_id=${tripId}`)).json();
    const beforeReadings = await readings();
    await page.locator('#odometer-history button').filter({hasText:'修改'}).first().click();
    await page.locator('#odometer-sheet.open').waitFor();
    await page.locator('#odometer-form [name=note]').fill('浏览器隔离修改验证');
    const modified = page.waitForResponse(r=>r.url().includes('/api/odometer-readings/')&&r.request().method()==='PUT');
    await page.locator('#odometer-form button[type=submit]').click();
    assert.equal((await modified).ok(),true);
    await page.waitForFunction(()=>!document.querySelector('#odometer-sheet').classList.contains('open'));
    assert.equal((await readings()).length,beforeReadings.length,'editing must not create another reading');
    await page.locator('#odometer-history button').filter({hasText:'修改'}).first().click();
    await page.keyboard.press('Escape');
    await page.locator('#open-odometer-sheet').click();
    assert.equal(await page.locator('#odometer-form [name=odometer]').inputValue(),'','new reading must reset old edit');
    const newMileage = Math.max(...beforeReadings.map(r=>r.odometer))+1;
    await page.locator('#odometer-form [name=odometer]').fill(String(newMileage));
    const created = page.waitForResponse(r=>r.url().endsWith('/api/odometer-readings')&&r.request().method()==='POST');
    await page.locator('#odometer-form button[type=submit]').click();
    assert.equal((await created).ok(),true);
    await page.waitForFunction(()=>!document.querySelector('#odometer-sheet').classList.contains('open'));
    assert.equal((await readings()).length,beforeReadings.length+1,'new must create exactly one reading');
    await page.locator('#finish-card summary').click();
    assert.equal(await page.locator('#finish-form button[type=submit]').isDisabled(),true);
    await page.locator('#finish-form [name=end_odometer]').fill(String(newMileage+100));
    await page.locator('#finish-form [name=confirm_finish]').check();
    assert.equal(await page.locator('#finish-form button[type=submit]').isDisabled(),false);
    assert.deepEqual(errors,[]);
    assert.deepEqual(layoutFailures,[]);
    console.log(`Screenshots: ${artifacts}`);
    console.log('PASS: 40 screen/viewport checks; real bill columns/readability; touch targets; short-height sticky close; modal focus/scroll/Tab; parsing; fold draft continuity; confirmation dismiss/accept and real network failure; source-edit refresh/return; late response cancellation; successful reparse reload and stable ID; save-in-flight guard; odometer PUT/POST and counts; finish guard. No production writes.');
  } finally { if(browser) await browser.close(); if(server) server.kill('SIGTERM'); }
})().catch(e=>{console.error(e);process.exitCode=1;});

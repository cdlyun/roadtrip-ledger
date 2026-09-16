// Full UI/API acceptance check. It only starts a disposable loopback server with
// a temporary SQLite database; it never reads or writes a user's configured DB.
const assert=require('node:assert/strict');
const fs=require('node:fs'), os=require('node:os'), path=require('node:path');
const {spawn,execFileSync}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const ROOT=path.resolve(__dirname,'..'); let server;
const artifacts=process.env.TEST_ARTIFACTS||fs.mkdtempSync(path.join(os.tmpdir(),'roadtrip-full-artifacts-'));

function start(){
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'roadtrip-full-db-'));
  const code='import app; app.init_db(); s=app.ThreadingHTTPServer(("127.0.0.1",0),app.Handler); print("BASE=http://127.0.0.1:"+str(s.server_port),flush=True); s.serve_forever()';
  server=spawn(process.env.PYTHON||'python3',['-u','-c',code],{cwd:ROOT,env:{...process.env,ROADTRIP_DB:path.join(dir,'fixture.db'),PYTHONDONTWRITEBYTECODE:'1'},stdio:['ignore','pipe','pipe']});
  return new Promise((resolve,reject)=>{let out=''; const timer=setTimeout(()=>reject(new Error(`server startup timed out: ${out}`)),10000); server.stdout.on('data',b=>{out+=b; const m=out.match(/BASE=(http:\/\/127\.0\.0\.1:\d+)/); if(m){clearTimeout(timer);resolve(m[1]);}}); server.once('error',reject); server.once('exit',c=>reject(new Error(`fixture server stopped (${c}): ${out}`)));});
}
const now=(offsetSeconds=0)=>new Date(Date.now()+offsetSeconds*1000).toISOString().slice(0,19);
async function body(response,label){assert.ok(response.ok(),`${label}: ${response.status()} ${await response.text()}`); return response.json();}
async function api(request,method,url,data,label=url){return body(await request[method](url,{data}),label);}
function entry(trip,category,n=1){return {trip_id:trip,raw_text:`${category} ${n}`,client_id:`full-${category}-${n}`,client_revision:1,recognized:{category,amount:n+10,occurred_at:now(),location:'隔离地点',item:`${category} 项目`}};}

async function apiLifecycle(request,base){
  const trip=await api(request,'post',`${base}/api/trips`,{name:'全量隔离行程',origin:'成都',destination:'兰州',departure_date:'2026-09-16',planned_days:3,start_odometer:1000},'create trip');
  const categories=['fuel','toll','parking','lodging','meal','ticket','daily','transport','service','shopping','clothing','vehicle','other'];
  const records=[];
  for(const [index,category] of categories.entries()){
    const p=entry(trip.id,category,index+1);
    if(category==='fuel') Object.assign(p.recognized,{fuel_grade:95,fuel_liters:10,odometer:1010,full_tank:true,item:null});
    records.push(await api(request,'post',`${base}/api/entries`,p,`create ${category}`));
  }
  assert.equal((await api(request,'get',`${base}/api/dashboard?trip_id=${trip.id}`,undefined,'dashboard')).entries.length,13,'all categories persisted');
  const meal=records.find(r=>r.category==='meal');
  const changed=await api(request,'put',`${base}/api/entries/${meal.id}`,{...entry(trip.id,'meal',99),id:meal.id,client_id:meal.client_id,client_revision:2,recognized:{...entry(trip.id,'meal',99).recognized,amount:99}},'update entry');
  assert.equal(changed.amount,99); assert.ok((await api(request,'get',`${base}/api/entries/${meal.id}/revisions`,undefined,'entry revisions')).length>=2);
  await api(request,'delete',`${base}/api/entries/${meal.id}`,{trip_id:trip.id,client_id:meal.client_id,client_revision:3},'delete entry');
  let trash=await api(request,'get',`${base}/api/trash?trip_id=${trip.id}`,undefined,'entry trash'); assert.ok(trash.records.some(r=>r.id===meal.id));
  await api(request,'post',`${base}/api/entries/${meal.id}/restore`,{trip_id:trip.id,client_id:meal.client_id,client_revision:4},'restore entry');
  await api(request,'delete',`${base}/api/entries/${meal.id}`,{trip_id:trip.id,client_id:meal.client_id,client_revision:5},'delete entry again');
  await api(request,'delete',`${base}/api/trash/entries/${meal.id}`,{trip_id:trip.id,client_id:meal.client_id,client_revision:5},'permanently delete entry');
  const odometerTime=now(60);
  const odo=await api(request,'post',`${base}/api/odometer-readings`,{trip_id:trip.id,odometer:1020,occurred_at:odometerTime,location:'隔离地点',note:'初始',client_id:'full-odo',client_revision:1},'create odometer');
  await api(request,'put',`${base}/api/odometer-readings/${odo.id}`,{trip_id:trip.id,odometer:1021,occurred_at:odometerTime,location:'隔离地点',note:'修改',client_id:'full-odo',client_revision:2},'update odometer');
  assert.ok((await api(request,'get',`${base}/api/odometer-readings/${odo.id}/revisions`,undefined,'odometer revisions')).length>=2);
  await api(request,'delete',`${base}/api/odometer-readings/${odo.id}`,{trip_id:trip.id,client_id:'full-odo',client_revision:3},'delete odometer');
  await api(request,'post',`${base}/api/odometer-readings/${odo.id}/restore`,{trip_id:trip.id,client_id:'full-odo',client_revision:4},'restore odometer');
  await api(request,'delete',`${base}/api/odometer-readings/${odo.id}`,{trip_id:trip.id,client_id:'full-odo',client_revision:5},'delete odometer again');
  await api(request,'delete',`${base}/api/trash/odometer-readings/${odo.id}`,{trip_id:trip.id,client_id:'full-odo',client_revision:5},'permanently delete odometer');
  const duplicate=entry(trip.id,'other',88); await api(request,'post',`${base}/api/entries`,duplicate,'duplicate seed');
  const duplicateResponse=await request.post(`${base}/api/entries`,{data:{...duplicate,client_id:'full-duplicate-confirm'}}); assert.equal(duplicateResponse.status(),400,'duplicate requires explicit confirmation'); assert.match((await duplicateResponse.json()).error,/可能与最近记录重复/);
  await api(request,'post',`${base}/api/entries`,{...duplicate,client_id:'full-duplicate-confirm',confirm_duplicate:true},'duplicate confirmation');
  const xlsx=await request.get(`${base}/api/export.xlsx?trip_id=${trip.id}`); assert.equal(xlsx.status(),200); assert.match(xlsx.headers()['content-type'],/spreadsheet/); const bytes=await xlsx.body(); assert.equal(bytes.subarray(0,2).toString(),'PK','Excel is a ZIP workbook');
  const workbookPath=path.join(artifacts,'roadtrip-ledger-export.xlsx'); fs.writeFileSync(workbookPath,bytes);
  const zipAudit=String.raw`import json, sys, zipfile, xml.etree.ElementTree as ET
p=sys.argv[1]
with zipfile.ZipFile(p) as z:
    workbook=ET.fromstring(z.read('xl/workbook.xml'))
    ns={'x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    sheets=[n.attrib['name'] for n in workbook.findall('x:sheets/x:sheet', ns)]
    text=''.join(z.read(n).decode('utf-8', 'ignore') for n in z.namelist() if n.startswith('xl/') and n.endswith('.xml'))
print(json.dumps({'sheets':sheets, 'has_trip_name':'全量隔离行程' in text, 'has_expenses':'消费明细' in text}, ensure_ascii=False))`;
  const audit=JSON.parse(execFileSync(process.env.PYTHON||'python3',['-c',zipAudit,workbookPath],{encoding:'utf8'}));
  assert.deepEqual([...audit.sheets].sort(),['行程汇总','消费明细','里程记录','每日行程','回收站','修改历史'].sort(),'Excel has exactly the six documented sheets'); assert.equal(audit.has_trip_name,true,'Excel contains the selected trip data'); assert.equal(audit.has_expenses,true,'Excel workbook labels its expense export');
  const updated=await api(request,'put',`${base}/api/trips/${trip.id}`,{name:'已修改行程',start_odometer:999,departure_date:'2026-09-16',planned_days:4},'update trip'); assert.equal(updated.name,'已修改行程');
  const finished=await api(request,'post',`${base}/api/trips/${trip.id}/finish`,{end_odometer:1100},'finish trip'); assert.equal(finished.status,'finished');
  assert.equal((await api(request,'post',`${base}/api/trips/${trip.id}/reopen`,{},'reopen trip')).status,'active');
  // Leave no active trip behind so the independent browser creation flow is real.
  await api(request,'post',`${base}/api/trips/${trip.id}/finish`,{end_odometer:1100},'finish reopened trip');
  return trip;
}

async function uiChecks(browser,base){
  const context=await browser.newContext({acceptDownloads:true,serviceWorkers:'block',viewport:{width:344,height:683}});
  await context.addInitScript(()=>{
    Object.defineProperty(window,'isSecureContext',{value:true,configurable:true});
    const listeners={}; const viewport={height:683,addEventListener:(type,fn)=>(listeners[type]??=[]).push(fn)};
    Object.defineProperty(window,'visualViewport',{value:viewport,configurable:true});
    window.__setKeyboardViewport=(height,offsetTop=0)=>{viewport.height=height;viewport.offsetTop=offsetTop;(listeners.resize||[]).forEach(fn=>fn(new Event('resize')));(listeners.scroll||[]).forEach(fn=>fn(new Event('scroll')));};
    window.__setGps=result=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition:(ok,fail)=>result.ok?ok({coords:result.coords}):fail({code:result.code})}});
  });
  const page=await context.newPage(); const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  const ensureTrashOpen=async()=>{const trash=page.locator('#trash-card'); if(!await trash.evaluate(el=>el.open)) await trash.locator('summary').click();};
  await page.goto(base); await page.locator('#trip-empty:not(.hidden)').waitFor();
  await page.locator('#trip-form [name=name]').fill('UI 行程'); await page.locator('#trip-form [name=origin]').fill('成都'); await page.locator('#trip-form [name=departure_date]').fill('2026-09-16'); await page.locator('#trip-form [name=start_odometer]').fill('2000'); await page.locator('#trip-form button[type=submit]').click(); await page.locator('#trip-active:not(.hidden)').waitFor();
  for(const target of ['trip','home','bills','stats']){await page.locator(`.bottom-nav [data-go-screen=${target}]`).click();await page.locator(`.app-screen[data-screen=${target}].active`).waitFor();}
  await page.locator('.bottom-nav [data-go-screen=trip]').click(); await page.locator('#edit-trip').click(); await page.locator('#trip-edit-form [name=name]').fill('UI 行程已修改'); await page.locator('#trip-edit-form button[type=submit]').click(); await page.waitForFunction(()=>document.querySelector('#trip-name')?.textContent==='UI 行程已修改'); assert.equal(await page.locator('#trip-edit').evaluate(el=>el.classList.contains('hidden')),true,'trip editor closes after save');
  await page.locator('#open-record-sheet').click(); await page.locator('#entry-mode-manual').click(); await page.locator('.quick-category[data-category=meal]').click();
  const quick=await page.locator('.quick-category').evaluateAll(nodes=>nodes.map(n=>n.dataset.category)); assert.equal(quick.length,12,'12 quick category buttons');
  await page.locator('#entry-form [name=category]').selectOption('other'); assert.equal(await page.locator('#entry-form [name=category]').inputValue(),'other','other category selectable');
  for(const scenario of [{ok:true,coords:{latitude:39.9042,longitude:116.4074,accuracy:9}},{ok:false,code:1},{ok:false,code:3}]){await page.evaluate(s=>window.__setGps(s),scenario);await page.locator('#gps-location').click();if(scenario.ok) await page.waitForFunction(()=>document.querySelector('#gps-status').textContent.includes('GPS'));else await page.waitForFunction(c=>document.querySelector('#gps-status').textContent.includes(c),scenario.code===1?'未获得定位权限':'定位超时');}
  const keyboard={height:360,offsetTop:20}; await page.evaluate(v=>window.__setKeyboardViewport(v.height,v.offsetTop),keyboard); await page.waitForFunction(()=>document.documentElement.style.getPropertyValue('--visual-height')==='360px'&&document.documentElement.style.getPropertyValue('--visual-bottom-inset')==='303px'); await page.locator('#record-sheet').evaluate(el=>{el.scrollTop=el.scrollHeight;}); await page.locator('#save-entry').evaluate(el=>{el.focus();el.scrollIntoView({block:'nearest'});}); const close=await page.locator('#record-sheet .sheet-close').boundingBox(), save=await page.locator('#save-entry').boundingBox(), visibleBottom=keyboard.offsetTop+keyboard.height; assert.ok(close&&close.y>=keyboard.offsetTop&&close.y+close.height<=visibleBottom,'keyboard mock leaves close button in the visual viewport'); assert.ok(save&&save.y>=keyboard.offsetTop&&save.y+save.height<=visibleBottom,'keyboard mock can scroll and focus the bottom save button into the visual viewport');
  for(const size of [{width:344,height:683},{width:711,height:584}]){await page.setViewportSize(size);await page.waitForTimeout(100);const geometry=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,width:innerWidth,rail:document.querySelector('.bottom-nav').getBoundingClientRect().width}));assert.ok(geometry.scroll<=geometry.width+1,`${size.width}px has no horizontal overflow`);if(size.width===711)assert.ok(Math.abs(geometry.rail-88)<2,'711px has desktop rail');}
  // Each quick category must start a fresh visible manual flow and reach a real
  // POST, rather than merely changing a hidden form field in one shared review.
  await page.locator('#record-sheet .sheet-close').click(); await page.locator('#record-sheet:not(.open)').waitFor(); await page.waitForFunction(()=>JSON.parse(localStorage.getItem('roadtrip.localState.v26')||'{}').draft!=null);
  // Clear the preflight draft through the visible product control so every
  // category below starts from a genuine empty manual-entry state.
  await page.reload(); await page.locator('#trip-active:not(.hidden)').waitFor(); await page.locator('#open-record-sheet').click(); await page.locator('#draft-notice:not(.hidden)').waitFor(); await page.locator('#draft-notice button').filter({hasText:'丢弃草稿'}).click(); await page.locator('#draft-notice').waitFor({state:'hidden'}); await page.locator('#record-sheet .sheet-close').click(); await page.locator('#record-sheet:not(.open)').waitFor();
  for(const [index,category] of [...quick,'other'].entries()){
    await page.locator('#open-record-sheet').click(); await page.locator('#entry-mode-manual').click();
    if(category==='other'){await page.locator('.quick-category[data-category=meal]').click(); await page.locator('#entry-form [name=category]').selectOption('other');} else await page.locator(`.quick-category[data-category=${category}]`).click();
    assert.equal(await page.locator('#entry-form [name=category]').inputValue(),category);
    await page.locator('#entry-form [name=amount]').fill(String(300+index));
    if(category!=='fuel') await page.locator('#entry-form [name=item]').fill(`UI ${category}`);
    const saved=page.waitForResponse(r=>r.url().endsWith('/api/entries')&&r.request().method()==='POST'); await page.locator('#save-entry').click(); assert.equal((await saved).ok(),true,`${category} manual entry saves`); await page.locator('#entry-card:not(.hidden)').waitFor(); await page.waitForFunction(()=>document.querySelector('#toast')?.textContent.includes('已经入账')); await page.locator('#record-sheet .sheet-close').click(); await page.locator('#record-sheet:not(.open)').waitFor();
  }
  // The second matching UI entry must present the browser confirmation and
  // persist only after acceptance.
  for(let attempt=0;attempt<2;attempt++){
    await page.locator('#open-record-sheet').click(); await page.locator('#entry-mode-manual').click(); await page.locator('.quick-category[data-category=meal]').click(); await page.locator('#entry-form [name=amount]').fill('777'); await page.locator('#entry-form [name=location]').fill('重复确认地点'); await page.locator('#entry-form [name=item]').fill('重复确认餐');
    if(attempt) page.once('dialog',dialog=>dialog.accept());
    const created=page.waitForResponse(r=>r.url().endsWith('/api/entries')&&r.request().method()==='POST'&&r.request().postData()?.includes('777')&&r.status()===201); await page.locator('#save-entry').click(); assert.equal((await created).ok(),true,'duplicate confirmation eventually saves'); await page.waitForFunction(()=>document.querySelector('#toast')?.textContent.includes('已经入账')); await page.locator('#record-sheet .sheet-close').click();
  }
  // Exercise entry actions from the rendered history, including revision UI,
  // edit, soft delete, restore, another delete, and permanent deletion.
  await page.locator('.bottom-nav [data-go-screen=bills]').click(); await page.locator('#history .history-item').first().waitFor();
  const excelLink=page.locator('#history-card a[href="/api/export.xlsx"]'); assert.equal(await excelLink.getAttribute('href'),'/api/export.xlsx','Excel UI keeps the active-trip default export URL'); const excelResponse=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/export.xlsx'); const [excel]=await Promise.all([page.waitForEvent('download'),excelLink.click()]); assert.equal((await excelResponse).status(),200,'Excel link receives a successful attachment response'); const excelPath=path.join(artifacts,excel.suggestedFilename()); await excel.saveAs(excelPath); assert.equal(fs.readFileSync(excelPath).subarray(0,2).toString(),'PK','Excel UI link downloads a workbook');
  const localDownload=page.waitForEvent('download'); await page.locator('#export-local').click(); const local=await localDownload; const localPath=path.join(artifacts,local.suggestedFilename()); await local.saveAs(localPath); assert.ok(fs.readFileSync(localPath).toString('utf8').startsWith('\uFEFF状态'),'local export UI downloads CSV');
  const firstEntry=page.locator('#history .history-item').first(); const revisions=page.waitForResponse(r=>/\/api\/entries\/\d+\/revisions$/.test(new URL(r.url()).pathname)); await firstEntry.getByRole('button',{name:'修订'}).click(); assert.equal((await revisions).ok(),true,'entry revision button loads history');
  await firstEntry.getByRole('button',{name:'修改'}).click(); await page.locator('#entry-form [name=amount]').fill('888'); const entryUpdate=page.waitForResponse(r=>/\/api\/entries\/\d+$/.test(new URL(r.url()).pathname)&&r.request().method()==='PUT'); await page.locator('#save-entry').click(); assert.equal((await entryUpdate).ok(),true,'entry edit uses PUT'); await page.waitForFunction(()=>document.querySelector('#toast')?.textContent.includes('修改已保存')); await page.locator('#record-sheet .sheet-close').click();
  const deleteEntry=page.waitForResponse(r=>r.url().endsWith('/api/sync')&&r.request().method()==='POST'); page.once('dialog',d=>d.accept()); await page.locator('#history .history-item').first().getByRole('button',{name:'删除'}).click(); assert.equal((await deleteEntry).ok(),true,'entry delete syncs'); await ensureTrashOpen(); await page.locator('#trash-list button').filter({hasText:'恢复'}).first().waitFor();
  const restoreEntry=page.waitForResponse(r=>/\/api\/entries\/\d+\/restore$/.test(new URL(r.url()).pathname)); await page.locator('#trash-list button').filter({hasText:'恢复'}).first().click(); assert.equal((await restoreEntry).ok(),true,'entry restore uses UI');
  const deleteAgain=page.waitForResponse(r=>r.url().endsWith('/api/sync')&&r.request().method()==='POST'); page.once('dialog',d=>d.accept()); await page.locator('#history .history-item').first().getByRole('button',{name:'删除'}).click(); await deleteAgain; await page.locator('#trash-list button').filter({hasText:'永久删除'}).first().waitFor(); const purgeEntry=page.waitForResponse(r=>/\/api\/trash\/entries\/\d+$/.test(new URL(r.url()).pathname)&&r.request().method()==='DELETE'); page.once('dialog',d=>d.accept()); await page.locator('#trash-list button').filter({hasText:'永久删除'}).first().click(); assert.equal((await purgeEntry).ok(),true,'entry permanent delete uses UI');
  await page.locator('.bottom-nav [data-go-screen=trip]').click(); await page.locator('#open-odometer-sheet').click(); await page.locator('#odometer-form [name=odometer]').fill('2050'); const odoCreate=page.waitForResponse(r=>r.url().endsWith('/api/odometer-readings')&&r.request().method()==='POST'); await page.locator('#odometer-form button[type=submit]').click(); assert.equal((await odoCreate).ok(),true,'odometer create uses UI'); await page.locator('#odometer-sheet:not(.open)').waitFor(); await page.waitForFunction(()=>document.querySelector('#toast')?.textContent.includes('里程已保存'));
  const firstOdo=page.locator('#odometer-history .history-item').first(); await firstOdo.getByRole('button',{name:'修改'}).click(); await page.locator('#odometer-form [name=note]').fill('UI 修改'); const odoUpdate=page.waitForResponse(r=>/\/api\/odometer-readings\/\d+$/.test(new URL(r.url()).pathname)&&r.request().method()==='PUT'); await page.locator('#odometer-form button[type=submit]').click(); assert.equal((await odoUpdate).ok(),true,'odometer edit uses PUT'); await page.locator('#odometer-sheet:not(.open)').waitFor();
  const deleteOdo=page.waitForResponse(r=>/\/api\/odometer-readings\/\d+$/.test(new URL(r.url()).pathname)&&r.request().method()==='DELETE'); page.once('dialog',d=>d.accept()); await page.locator('#odometer-history .history-item').first().getByRole('button',{name:'删除'}).click(); assert.equal((await deleteOdo).ok(),true,'odometer delete uses DELETE'); await page.locator('.bottom-nav [data-go-screen=bills]').click(); await ensureTrashOpen(); await page.locator('#trash-list button').filter({hasText:'恢复'}).first().waitFor(); const restoreOdo=page.waitForResponse(r=>/\/api\/odometer-readings\/\d+\/restore$/.test(new URL(r.url()).pathname)); await page.locator('#trash-list button').filter({hasText:'恢复'}).first().click(); assert.equal((await restoreOdo).ok(),true,'odometer restore uses UI');
  await page.locator('.bottom-nav [data-go-screen=trip]').click(); const deleteOdoAgain=page.waitForResponse(r=>/\/api\/odometer-readings\/\d+$/.test(new URL(r.url()).pathname)&&r.request().method()==='DELETE'); page.once('dialog',d=>d.accept()); await page.locator('#odometer-history .history-item').first().getByRole('button',{name:'删除'}).click(); await deleteOdoAgain; await page.locator('.bottom-nav [data-go-screen=bills]').click(); await ensureTrashOpen(); await page.locator('#trash-list button').filter({hasText:'永久删除'}).first().waitFor(); const purgeOdo=page.waitForResponse(r=>/\/api\/trash\/odometer-readings\/\d+$/.test(new URL(r.url()).pathname)&&r.request().method()==='DELETE'); page.once('dialog',d=>d.accept()); await page.locator('#trash-list button').filter({hasText:'永久删除'}).first().click(); assert.equal((await purgeOdo).ok(),true,'odometer permanent delete uses UI');
  await page.locator('.bottom-nav [data-go-screen=trip]').click(); await page.locator('#finish-card summary').click(); await page.locator('#finish-form [name=end_odometer]').fill('2100'); await page.locator('#finish-form [name=confirm_finish]').check(); page.once('dialog',d=>d.accept()); await page.locator('#finish-form button[type=submit]').click(); await page.waitForFunction(()=>document.querySelector('#toast')?.textContent.includes('行程已结束')); await page.locator('.bottom-nav [data-go-screen=home]').click(); await page.locator('#reopen-trip').waitFor(); await page.locator('#reopen-trip').click(); await page.locator('#trip-active:not(.hidden)').waitFor();
  assert.deepEqual(errors,[]); await context.close();
}

(async()=>{let browser;try{const base=await start();browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});const context=await browser.newContext();await apiLifecycle(context.request,base);await context.close();await uiChecks(browser,base);console.log('PASS: isolated API lifecycle, all 13 categories, revisions/trash/restore/purge, duplicate confirmation, Excel, trip lifecycle, GPS mocks, viewport/keyboard reachability, and static/dynamic UI controls.');}finally{if(browser)await browser.close();if(server)server.kill('SIGTERM');}})().catch(error=>{console.error(error);process.exitCode=1;});

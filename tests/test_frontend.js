// v26 前端离线安全回归：用真实监听器、共享存储和共享 Web Lock 模拟两个标签页。
const assert=require("assert"), fs=require("fs"), vm=require("vm");
const html=fs.readFileSync("static/index.html","utf8");
let source=fs.readFileSync("static/app.js","utf8");
const styles=fs.readFileSync("static/styles.css","utf8");
assert.match(styles,/input,select,textarea\{[^}]*min-height:50px/);
assert.match(styles,/\.metric\{[^}]*color:var\(--ink\)/);
assert.match(styles,/\.metric b,\.metric strong\{[^}]*color:var\(--ink\)/);
assert.match(html,/<button class="journey-more" data-go-screen="trip" type="button">[\s\S]*?<strong>查看行程与里程<\/strong>[\s\S]*?路线、设置与里程记录[\s\S]*?<span class="journey-more__icon" aria-hidden="true">›<\/span>[\s\S]*?<\/button>/);
assert.match(styles,/\.journey-more\{[^}]*min-height:50px[^}]*border:1px solid rgba\(255,255,255,\.38\)[^}]*text-align:left/);
assert.match(styles,/\.journey-more:focus-visible\{[^}]*outline:3px solid/);
assert.match(styles,/@container \(min-width:720px\)\{[^]*?\.journey-more\{[^}]*max-width:340px/);
assert.ok(source.includes('document.querySelectorAll("[data-go-screen]").forEach(button=>button.addEventListener("click",()=>{ closeSheets(); setScreen(button.dataset.goScreen); }));'));
assert.match(styles,/input\[type=checkbox\]\{[^}]*width:22px[^}]*min-height:22px/);
assert.match(styles,/\.finish-confirm\{display:flex[^}]*align-items:flex-start/);
assert.match(styles,/\.stats-cover h2\{[^}]*font-size:30px/);
assert.match(styles,/height:calc\(84px \+ var\(--safe-bottom\)\)/);
assert.match(styles,/@media \(min-width:992px\)\{[^]*?\.app-shell\{[^}]*padding-left:232px/);
assert.match(styles,/@media \(min-width:720px\) and \(max-width:991px\)\{[^]*?\.bottom-sheet\{width:min\(640px,calc\(100% - 32px\)\)/);
assert.match(styles,/#app-main\{[^}]*container-type:inline-size/);
assert.match(styles,/@container \(min-width:720px\)\{[^]*?\.app-screen\.active\{[^}]*grid-template-columns:minmax\(0,\.88fr\) minmax\(0,1\.12fr\)/);
assert.match(styles,/@container \(min-width:720px\)\{[^]*?\[data-screen="bills"\]>#history-card\{grid-column:1\}[^]*?\[data-screen="bills"\]>#trash-card\{grid-column:2\}/);
assert.match(html,/id="entry-mode-natural"[^>]*>一句话记账/);
assert.match(html,/id="entry-mode-manual"[^>]*>手动填写/);
assert.match(html,/这一路，越山向海/);
assert.doesNotMatch(html,/这段路，花得明明白白/);
assert.match(source,/error\.uncertain\s*=\s*true/);
assert.match(source,/error\.uncertain \? "请求超时，结果待核对"/);
assert.match(source,/function scheduleNaturalParse\(\)/);
assert.match(source,/api\("\/api\/parse"/);
assert.match(source,/window\.addEventListener\?\.\("offline",\(\)=>setOnline\(false\)\)/);
source=source.replace(/fillSelect\(\); bootstrap\(\);[\s\S]*$/,"globalThis.__test={state,offlineQueue,localState,readV26,ensureLocalStateLocked,mutateLocalState,syncOfflineQueue,writeStored,renderReview,persistDraft,currentDraft,draftFromForm,scheduleDraftSave,resumeDraft,resetReview,advanceReviewRecord,removePending,loadTrash,renderTrash,removeOdometer,restoreTrash,permanentlyDeleteTrash,openTripEdit,editPending,editEntry,editOdometer,saveOdometer,verifyEntryResponse,verifySyncResponse,markFieldConfirmed,renderDashboard,localCsvRows,exportLocalBomCsv,parseNaturalEntry,scheduleNaturalParse,setEntryMode,closeSheets,openOdometerSheet,openPendingList,updateFinishControl,finishRequirements,setSheetOpen,sheetFocusable,trapSheetFocus,renderFieldMeta,editSourceText,returnToReview,canReidentifySource,syncVisualViewport};");

class Node {
  constructor(selector=""){ this.selector=selector; this.name=(selector.match(/\[name=([^\]]+)\]/)||[])[1] || ""; this.style={}; this.className=""; const classes=new Set(); this.classList={add:(...names)=>names.forEach(name=>classes.add(name)),remove:(...names)=>names.forEach(name=>classes.delete(name)),toggle:(name,force)=>{ const next=force===undefined ? !classes.has(name) : Boolean(force); next?classes.add(name):classes.delete(name); return next; },contains:name=>classes.has(name)}; this.children=[]; this.listeners={}; this.value=""; this.textContent=""; this.disabled=false; this.dataset={}; this.files=[]; this._formData={}; this.checked=false; }
  append(...nodes){ this.children.push(...nodes); }
  appendChild(node){ this.children.push(node); return node; }
  replaceChildren(...nodes){ this.children=[...nodes]; }
  reset(){ this._formData={}; this.value=""; }
  addEventListener(type,fn){ (this.listeners[type] ||= []).push(fn); }
  dispatch(type,event={}){ const target=event.target || this; return Promise.all((this.listeners[type]||[]).map(fn=>fn({...event,target,currentTarget:event.currentTarget || this,preventDefault(){}}))); }
  querySelector(selector){ return document.querySelector(`${this.selector} ${selector}`); }
  querySelectorAll(){ return this._queryNodes || []; }
  scrollIntoView(){}
  focus(){ this.focused=true; if(global.document) global.document.activeElement=this; }
  click(){ this.clicked=true; }
  remove(){ this.removed=true; }
  contains(node){ return node===this || this.children.includes(node); }
  setAttribute(name,value){ (this.attributes ||= {})[name]=value; }
  removeAttribute(name){ if(this.attributes) delete this.attributes[name]; }
  getClientRects(){ return this._rects || []; }
}

const values=new Map(), nodes=new Map();
const needed=["#connection","#toast","#metrics","#history","#category-summary","#pending-history","#sync-note","#sync-progress","#sync-progress-label","#sync-progress-bar","#entry-form","#entry-text","#review-card","#review-title","#save-state","#save-entry","#cancel-edit","#missing-list","#field-meta","#record-list","#record-progress","#gps-location","#gps-status","#fuel-unit-price","#fuel-fields","#item-field","#trip-edit","#trip-edit-form","#trip-form","#trip-empty","#trip-active","#entry-card","#finish-card","#finish-form","#history-card","#odometer-card","#odometer-form","#odometer-history","#trash-card","#trash-list","#draft-notice","#export-local","#record-sheet","#odometer-sheet","#sheet-backdrop"];
for(const key of needed) nodes.set(key,new Node(key));
for(const field of ["category","amount","location","item","occurred_at","fuel_grade","fuel_liters","fuel_unit_price","odometer"]) nodes.set(`#entry-form [name=${field}]`,new Node(`#entry-form [name=${field}]`));
for(const field of ["odometer","occurred_at","location","note"]) nodes.set(`#odometer-form [name=${field}]`,new Node(`#odometer-form [name=${field}]`));
for(const field of ["end_odometer","confirm_finish"]) nodes.set(`#finish-form [name=${field}]`,new Node(`#finish-form [name=${field}]`));
const body=new Node("body"), rootStyle={setProperty:(key,value)=>{rootStyle[key]=value;}};
function documentStub(){ return {body,documentElement:{style:rootStyle},activeElement:null,querySelector(key){ if(!nodes.has(key)) nodes.set(key,new Node(key)); return nodes.get(key); },querySelectorAll(){ return []; },createElement(tag){ return new Node(tag); },addEventListener(){}}; }
global.document=documentStub();
class FakeFormData { constructor(form){ this.form=form; } get(key){ return this.form?this.form._formData[key] ?? document.querySelector(`${this.form.selector} [name=${key}]`)?.value ?? "": ""; } }
const lockState={tail:Promise.resolve()};
const locks={request(_name,_options,work){ const run=lockState.tail.then(()=>work()); lockState.tail=run.catch(()=>{}); return run; }};
let fetchCalls=[]; let fetchImpl=async(path,options)=>{ fetchCalls.push({path,options}); return {ok:true,status:200,json:async()=>({status:"created"})}; };
const context={console,document:global.document,window:{innerHeight:683,addEventListener(){},isSecureContext:true,getComputedStyle:node=>node._computedStyle || {display:"block",visibility:"visible"}},navigator:{locks},localStorage:{getItem:k=>values.has(k)?values.get(k):null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)},crypto:{randomUUID:(()=>{let i=0;return()=>`client-${++i}`;})()},fetch:(...a)=>fetchImpl(...a),AbortController,setTimeout,clearTimeout,FormData:FakeFormData,confirm:()=>true,Blob:class{constructor(parts){this.parts=parts;}},URL:{createObjectURL:()=>"blob:test",revokeObjectURL(){}},setTimeout,clearTimeout};
vm.createContext(context); vm.runInContext(source,context); const t=context.__test;
context.window.visualViewport={height:360.5,offsetTop:20.25,addEventListener(){}}; t.syncVisualViewport(); assert.strictEqual(rootStyle["--visual-height"],"360.5px"); assert.strictEqual(rootStyle["--visual-bottom-inset"],"302.25px");
context.window.visualViewport={height:360,offsetTop:0,addEventListener(){}}; t.syncVisualViewport(); assert.strictEqual(rootStyle["--visual-bottom-inset"],"323px");
context.window.innerHeight=400; context.window.visualViewport={height:683,offsetTop:0,addEventListener(){}}; t.syncVisualViewport(); assert.strictEqual(rootStyle["--visual-height"],"400px"); assert.strictEqual(rootStyle["--visual-bottom-inset"],"0px");
context.window.innerHeight=683;
context.window.visualViewport=null; t.syncVisualViewport(); assert.strictEqual(rootStyle["--visual-height"],"683px"); assert.strictEqual(rootStyle["--visual-bottom-inset"],"0px");

function resetStorage(){ values.clear(); t.state.localState=null; t.state.trip=null; t.state.dashboard=null; t.state.parsed=null; t.state.reviewRecords=[]; t.state.reviewRecordClientIds=[]; t.state.reviewIndex=0; t.state.reviewQueueItem=null; t.state.editingEntryId=null; t.state.editingClientId=null; t.state.editingClientRevision=null; t.state.sourceEdit=null; t.state.online=false; t.state.trash=[]; fetchCalls=[]; }
function putState(doc){ values.set("roadtrip.localState.v26",JSON.stringify(doc)); t.state.localState=null; }
const entry=(id,trip=1)=>({entity:"entry",op:"upsert",client_id:id,payload:{trip_id:trip,raw_text:"午餐30元",recognized:{category:"meal",amount:30,item:"米粉"}},client_revision:1,queued_at:id,updated_at:id,error:null});
function syncResult(options){
  const request=JSON.parse(options?.body||"{}"), payload=request.payload||{}, fields=payload.recognized||payload, id=payload.id||99;
  return {status:"created",entity:request.entity,id,client_revision:request.client_revision,record:{...fields,id,trip_id:payload.trip_id,raw_text:payload.raw_text,note:fields.item??fields.note??null,client_revision:request.client_revision,deleted_at:null}};
}

(async()=>{
  // 里程编辑要立即打开弹层；之后点“新增”必须清除旧编辑态，避免覆盖原记录。
  resetStorage(); t.state.trip={id:7,start_odometer:100}; const editableOdo={id:19,trip_id:7,odometer:1200,location:"张掖",note:null,client_revision:1};
  t.editOdometer(editableOdo); assert.strictEqual(nodes.get("#odometer-sheet").classList.contains("open"),true); assert.strictEqual(nodes.get("#odometer-form [name=odometer]").value,1200);
  t.openOdometerSheet(); assert.strictEqual(nodes.get("#odometer-form [name=odometer]").value,"");

  // 关闭里程编辑后必须解除目标 id：紧接着保存应新建，而不是覆盖刚才的旧记录。
  t.editOdometer(editableOdo); t.closeSheets(); t.state.online=true; let odometerCreate=null;
  fetchImpl=async(path,options)=>{ fetchCalls.push({path,options}); if(path==="/api/odometer-readings"){ odometerCreate={path,options}; return {ok:true,status:200,json:async()=>({id:20,odometer:1300})}; } if(path.startsWith("/api/dashboard")) return {ok:true,status:200,json:async()=>({total_spend:0,vehicle_cost:0,distance_km:0,fuel:{status:"ok",l_per_100km:null},vehicle_cost_per_km:null,by_category:{},entries:[],odometer_readings:[]})}; throw new Error(path); };
  await t.saveOdometer({trip_id:7,odometer:1300,occurred_at:"2026-09-16T12:00",source:"manual",client_id:"new-odo",client_revision:1});
  assert.strictEqual(odometerCreate.path,"/api/odometer-readings"); assert.strictEqual(odometerCreate.options.method,"POST");

  // 弹层锁住页面滚动，关闭后焦点回到触发位置；这也是 Escape 关闭时的共同路径。
  const opener=new Node("opener"); document.activeElement=opener; t.setSheetOpen("odometer",true);
  assert.strictEqual(body.style.overflow,"hidden"); assert.strictEqual(nodes.get("#odometer-sheet").classList.contains("open"),true);
  t.setSheetOpen("odometer",false); assert.strictEqual(body.style.overflow,""); assert.strictEqual(document.activeElement,opener);

  // 这里只为焦点循环提供显式的布局 rect；假 DOM 不会把所有节点假定为可见。
  // review 中 display:none 的保存按钮不能成为“最后一个可聚焦元素”。
  const recordSheet=nodes.get("#record-sheet"), sheetClose=new Node("sheet-close"), visibleFirst=new Node("visible-first"), visibleLast=new Node("visible-last"), hiddenReviewSave=new Node("hidden-review-save");
  [sheetClose,visibleFirst,visibleLast].forEach(node=>{ node._rects=[{}]; node._computedStyle={display:"block",visibility:"visible"}; });
  hiddenReviewSave._rects=[]; hiddenReviewSave._computedStyle={display:"none",visibility:"hidden"};
  recordSheet._queryNodes=[sheetClose,visibleFirst,hiddenReviewSave,visibleLast]; recordSheet.children=[sheetClose,visibleFirst,hiddenReviewSave,visibleLast];
  t.setSheetOpen("record",true); document.activeElement=visibleLast; let prevented=false;
  t.trapSheetFocus({key:"Tab",preventDefault(){ prevented=true; }}); assert.strictEqual(prevented,true); assert.strictEqual(document.activeElement,sheetClose);
  document.activeElement=sheetClose; prevented=false; t.trapSheetFocus({key:"Tab",shiftKey:true,preventDefault(){ prevented=true; }}); assert.strictEqual(prevented,true); assert.strictEqual(document.activeElement,visibleLast);
  t.setSheetOpen("record",false); assert.strictEqual(recordSheet.attributes.inert,"");

  // 结束按钮只在有效里程、勾选确认和没有待处理时启用；提交处仍有同一组保护。
  const finish=nodes.get("#finish-form"), finishButton=finish.querySelector("button[type=submit]"), finishOdo=nodes.get("#finish-form [name=end_odometer]"), finishConfirm=nodes.get("#finish-form [name=confirm_finish]");
  finishOdo.value="99"; finishConfirm.checked=true; t.updateFinishControl(); assert.strictEqual(finishButton.disabled,true);
  finishOdo.value="150"; finishConfirm.checked=false; t.updateFinishControl(); assert.strictEqual(finishButton.disabled,true);
  finishConfirm.checked=true; putState({schema:26,revision:0,queue:[entry("finish-pending",7)],trash:[],draft:null}); t.updateFinishControl(); assert.strictEqual(finishButton.disabled,true);
  putState({schema:26,revision:0,queue:[],trash:[],draft:null}); t.updateFinishControl(); assert.strictEqual(finishButton.disabled,false);
  // 后补一笔更早时间但更高的里程时，后端仍以所有点的最大值拒绝结束；
  // 前端必须扫描 entries 和独立里程，不能只看按时间排序的 current_odometer。
  t.state.dashboard={current_odometer:140,entries:[{odometer:180}],odometer_readings:[{odometer:170}]};
  finishOdo.value="179"; t.updateFinishControl(); assert.strictEqual(finishButton.disabled,true);
  finishOdo.value="180"; t.updateFinishControl(); assert.strictEqual(finishButton.disabled,false);

  // 旧版队列迁移：写入 v26 后保留旧key，编号固定，成功同步只移除对应条目。
  resetStorage(); values.set("roadtrip.offlineQueue",JSON.stringify([{payload:{trip_id:1,recognized:{category:"meal",amount:30}},queued_at:"legacy"}]));
  t.state.online=true; fetchImpl=async(path,options)=>{ fetchCalls.push({path,options}); return {ok:true,status:200,json:async()=>syncResult(options)}; };
  await t.syncOfflineQueue(); const migrated=JSON.parse(values.get("roadtrip.localState.v26")); assert.strictEqual(migrated.schema,31); assert.strictEqual(migrated.queue.length,0); assert.ok(values.has("roadtrip.offlineQueue")); assert.strictEqual(fetchCalls[0].path,"/api/sync");

  // 两个标签页并发追加必须串行合并，不能互相覆盖。
  resetStorage(); putState({schema:26,revision:0,queue:[],trash:[],draft:null});
  const append=client=>t.mutateLocalState(doc=>{doc.queue.push(entry(client));}); await Promise.all([append("A"),append("B")]); assert.strictEqual(JSON.stringify(Array.from(t.offlineQueue().map(x=>x.client_id).sort())),JSON.stringify(["A","B"]));

  // 存储空间不足时写入失败并给出明确错误。
  const oldSet=context.localStorage.setItem; context.localStorage.setItem=()=>{throw new Error("quota")}; assert.throws(()=>t.writeStored("x",[]),/尚未保存/); context.localStorage.setItem=oldSet;

  // 草稿300ms持久化后只对同一行程可继续，跨行程不能套用。
  resetStorage(); t.state.trip={id:7}; t.state.parsed={raw_text:"兰州吃面30元",recognized:{category:"meal",amount:30,location:"兰州",item:"牛肉面",occurred_at:"2026-09-12T12:00"},missing:[],field_meta:{amount:{state:"recognized"}}}; t.state.reviewRecords=[t.state.parsed]; t.state.reviewIndex=0; t.state.draftClientId="draft-7"; await t.persistDraft(); assert.strictEqual(t.currentDraft().trip_id,7); t.state.trip={id:8}; assert.strictEqual(t.currentDraft(),null);

  // 多笔 records 逐笔展示，字段状态进入真实DOM节点。
  resetStorage(); t.renderReview({raw_text:"午餐30元，停车20元",records:[{recognized:{category:"meal",amount:30,item:"米粉"},field_meta:{category:{state:"recognized"},amount:{state:"recognized"}}},{recognized:{category:"parking",amount:20,item:"停车费"},field_meta:{category:{state:"derived"}}}],missing:[]}); assert.strictEqual(t.state.reviewRecords.length,2); assert.ok(nodes.get("#record-list").children.length>=2); assert.ok(nodes.get("#field-meta").children.length>=2);

  // 批量同步逐条调用统一端点并清空成功队列。
  resetStorage(); putState({schema:26,revision:0,queue:[entry("C"),entry("D")],trash:[],draft:null}); t.state.online=true; fetchImpl=async(path,options)=>{fetchCalls.push({path,options}); return {ok:true,status:200,json:async()=>syncResult(options)};}; await t.syncOfflineQueue(); assert.strictEqual(t.offlineQueue().length,0); assert.strictEqual(fetchCalls.length,2); assert.ok(fetchCalls.every(call=>call.path==="/api/sync"));

  // 服务端若回传了旧值，不得静默清理本地队列。
  resetStorage(); putState({schema:26,revision:0,queue:[entry("wrong-server-value")],trash:[],draft:null}); t.state.online=true;
  fetchImpl=async(path,options)=>{ const result=syncResult(options); result.record.amount=999; return {ok:true,status:200,json:async()=>result}; };
  await t.syncOfflineQueue(); assert.strictEqual(t.offlineQueue().length,1); assert.match(t.offlineQueue()[0].error,/未确认/);

  // 本地BOM CSV首列和离线字段保持冻结口径。
  resetStorage(); putState({schema:26,revision:0,queue:[entry("E")],trash:[],draft:null}); const rows=t.localCsvRows(); assert.strictEqual(rows[0][0],"状态"); assert.strictEqual(rows[0].length,16); assert.strictEqual(rows[1][1],"E");

  // 原始文字一输入就保存草稿，尚未解析也能在同一行程恢复。
  resetStorage(); t.state.trip={id:7}; nodes.get("#entry-text").value="在大柴旦买水 8 元"; await nodes.get("#entry-text").dispatch("input"); await new Promise(resolve=>setTimeout(resolve,360));
  let draftDoc=JSON.parse(values.get("roadtrip.localState.v26")); assert.strictEqual(draftDoc.draft.raw_text,"在大柴旦买水 8 元"); assert.strictEqual(draftDoc.draft.trip_id,7); assert.strictEqual(t.currentDraft().raw_text,"在大柴旦买水 8 元");

  // 一句话入口必须调用既有解析接口，并将分类、金额、地点和内容带入确认页。
  resetStorage(); t.state.trip={id:7}; nodes.get("#entry-text").value="在广元午餐吃米粉 30 元";
  fetchImpl=async(path,options)=>{ fetchCalls.push({path,options}); return {ok:true,status:200,json:async()=>({raw_text:"在广元午餐吃米粉 30 元",recognized:{category:"meal",amount:30,location:"广元",item:"米粉",occurred_at:"2026-09-15T12:00"},missing:[],field_meta:{category:{state:"recognized"},amount:{state:"recognized"},location:{state:"recognized"},item:{state:"recognized"}}})}; };
  await t.parseNaturalEntry(); assert.strictEqual(fetchCalls[0].path,"/api/parse"); assert.strictEqual(t.state.parsed.recognized.category,"meal"); assert.strictEqual(nodes.get("#entry-form [name=amount]").value,30); assert.strictEqual(nodes.get("#entry-form [name=location]").value,"广元"); assert.strictEqual(nodes.get("#entry-form [name=item]").value,"米粉");

  // 旧请求被新短文本取消后，识别按钮必须立即恢复，短文字仍可由用户手动提交。
  resetStorage(); t.state.trip={id:7}; nodes.get("#parse-entry").disabled=true; nodes.get("#entry-text").value="吃面"; t.scheduleNaturalParse(); assert.strictEqual(nodes.get("#parse-entry").disabled,false);

  // 旧请求在路上时，后输入的文字仍必须自动解析；旧响应不能覆盖最后一段文字。
  resetStorage(); t.state.trip={id:7}; let automaticCalls=0, resolveOld, resolveNew;
  const parseReply=(location,item,amount)=>({ok:true,status:200,json:async()=>({raw_text:`${location}${item}${amount}元`,recognized:{category:"meal",amount,location,item,occurred_at:"2026-09-15T12:00"},missing:[],field_meta:{category:{state:"recognized"},amount:{state:"recognized"}}})});
  fetchImpl=async()=>new Promise(resolve=>{ automaticCalls++; if(automaticCalls===1) resolveOld=()=>resolve(parseReply("广元","米粉",30)); else resolveNew=()=>resolve(parseReply("兰州","牛肉面",40)); });
  nodes.get("#entry-text").value="在广元午餐吃米粉 30 元"; t.scheduleNaturalParse(); await new Promise(resolve=>setTimeout(resolve,730)); assert.strictEqual(automaticCalls,1);
  nodes.get("#entry-text").value="在兰州午餐吃牛肉面 40 元"; t.scheduleNaturalParse(); resolveOld(); await new Promise(resolve=>setTimeout(resolve,730)); assert.strictEqual(automaticCalls,2); resolveNew(); await new Promise(resolve=>setTimeout(resolve,0));
  assert.strictEqual(t.state.parsed.recognized.location,"兰州"); assert.strictEqual(t.state.parsed.recognized.item,"牛肉面"); assert.strictEqual(t.state.parsed.recognized.amount,40);

  // 切换到手动填写或关闭面板会取消定时识别，旧响应也会因 token 失效而被忽略。
  resetStorage(); t.state.trip={id:7}; t.setEntryMode("natural"); nodes.get("#entry-text").value="在格尔木买水 8 元"; t.scheduleNaturalParse(); t.setEntryMode("manual"); await new Promise(resolve=>setTimeout(resolve,730)); assert.strictEqual(fetchCalls.length,0);

  // 多笔草稿恢复完整 records、当前索引和每笔固定 client_id，不重复生成编号。
  resetStorage(); t.state.trip={id:7}; const fixedRecords=[{recognized:{category:"meal",amount:30,item:"米粉"},field_meta:{amount:{state:"recognized"}}},{recognized:{category:"parking",amount:20,item:"停车费"},field_meta:{amount:{state:"review"}}}];
  t.renderReview({raw_text:"广元午餐30元，停车20元",records:fixedRecords,missing:[]},null,{recordIndex:1,recordClientIds:["fixed-a","fixed-b"]});
  t.state.reviewIndex=1; t.state.parsed=t.state.reviewRecords[1]; t.state.reviewRecordClientIds=["fixed-a","fixed-b"]; await t.persistDraft(); const multiDraft=JSON.parse(values.get("roadtrip.localState.v26")).draft;
  assert.deepStrictEqual(multiDraft.record_client_ids,["fixed-a","fixed-b"]); assert.strictEqual(multiDraft.record_index,1); assert.strictEqual(multiDraft.records.length,2); t.resetReview(); t.resumeDraft(multiDraft);
  assert.deepStrictEqual(t.state.reviewRecordClientIds,["fixed-a","fixed-b"]); assert.strictEqual(t.state.reviewIndex,1); assert.strictEqual(t.state.reviewRecords.length,2); assert.strictEqual(t.state.draftClientId,"fixed-b");

  // 已确认的多笔行只读，不能再次点击或提交；批次进度始终显示当前序号。
  t.renderReview({raw_text:"午餐30元，停车20元",records:fixedRecords,missing:[]},null,{recordClientIds:["fixed-a","fixed-b"]}); assert.strictEqual(nodes.get("#record-list").children[1].disabled,true); assert.strictEqual(nodes.get("#record-progress").textContent,"本句话识别到 2 笔消费，正在确认第 1/2 笔");
  assert.strictEqual(t.advanceReviewRecord(),true); assert.strictEqual(nodes.get("#record-list").children[0].disabled,true); assert.strictEqual(nodes.get("#record-list").children[1].disabled,false); assert.strictEqual(nodes.get("#record-progress").textContent,"本句话识别到 2 笔消费，正在确认第 2/2 笔");

  // field_meta 不只显示徽标，还给对应输入控件状态；用户输入后转为已确认。
  resetStorage(); t.state.trip={id:7}; const reviewField={raw_text:"午餐30元",recognized:{category:"meal",amount:30,item:"米粉"},field_meta:{amount:{state:"review",reason:"金额置信度较低"}}}; t.renderReview(reviewField); const amountControl=nodes.get("#entry-form [name=amount]"); assert.strictEqual(amountControl.dataset.fieldState,"review"); assert.strictEqual(amountControl.title,"金额置信度较低"); amountControl.value="31"; await nodes.get("#entry-form").dispatch("input",{target:amountControl}); assert.strictEqual(amountControl.dataset.fieldState,"certain"); assert.strictEqual(t.state.parsed.field_meta.amount.state,"recognized");

  // 字段提示仅面向当前分类实际展示的中文字段；内部键与餐饮无关油耗项不泄露。
  resetStorage(); t.state.trip={id:7}; t.renderReview({raw_text:"午餐30元",recognized:{category:"meal",amount:30,item:"米粉"},field_meta:{category_label:{state:"missing"},people:{state:"missing"},nights:{state:"missing"},note:{state:"missing"},fuel_grade:{state:"missing"},occurred_at:{state:"missing"},amount:{state:"recognized"}}});
  const metaText=nodes.get("#field-meta").children.map(node=>node.textContent).join("|"); assert.ok(!/category_label|people|nights|note|油号|待补充.*记录时间/.test(metaText)); assert.match(metaText,/金额：已识别/); assert.match(metaText,/记录时间：默认值/);
  resetStorage(); t.state.trip={id:7}; t.renderReview({raw_text:"加油300元",recognized:{category:"fuel",amount:300,occurred_at:"2026-09-15T12:00"},field_meta:{location:{state:"missing"},fuel_liters:{state:"missing"},odometer:{state:"missing"},fuel_unit_price:{state:"missing"},full_tank:{state:"missing"}}});
  const fuelMeta=nodes.get("#field-meta").children.map(node=>node.textContent).join("|"); assert.match(fuelMeta,/地点：可选/); assert.match(fuelMeta,/挂牌单价：可选/); assert.match(fuelMeta,/是否加满：可选/); assert.match(fuelMeta,/加油升数：影响统计/); assert.match(fuelMeta,/当前里程：影响统计/);
  // 解析安全缺口仍应阻止入账，只是不能把后端内部字段名暴露给用户。
  resetStorage(); t.state.trip={id:7}; t.renderReview({raw_text:"两笔消费",recognized:{category:"meal",amount:30,occurred_at:"2026-09-15T12:00"},missing:[{field:"multiple_entries",level:"required"}]}); assert.strictEqual(nodes.get("#save-entry").disabled,true); const safetyText=nodes.get("#missing-list").children[0].children[0].children[0].textContent; assert.match(safetyText,/包含多笔消费/); assert.ok(!/multiple_entries/.test(safetyText));

  // 重新识别只允许单条未提交新增；取消确认或解析失败都保留当前确认内容和原句。
  resetStorage(); t.state.trip={id:7}; nodes.get("#entry-text").value=""; t.renderReview({raw_text:"广元午餐30元",recognized:{category:"meal",amount:30,location:"广元",item:"米粉",occurred_at:"2026-09-15T12:00"},missing:[],field_meta:{}},null,{recordClientIds:["reparse-new"]});
  nodes.get("#entry-form [name=amount]").value="31"; await nodes.get("#entry-form").dispatch("input",{target:nodes.get("#entry-form [name=amount]")}); t.editSourceText(); assert.strictEqual(t.state.sourceEdit.clientId,"reparse-new"); assert.strictEqual(nodes.get("#entry-text").value,"广元午餐30元");
  nodes.get("#entry-text").value="广元午餐31元"; context.confirm=()=>false; await t.parseNaturalEntry(); assert.strictEqual(fetchCalls.length,0); assert.strictEqual(t.state.parsed.recognized.amount,31); t.returnToReview(); assert.strictEqual(nodes.get("#entry-text").value,"广元午餐30元"); assert.strictEqual(nodes.get("#entry-form [name=amount]").value,31);
  context.confirm=()=>true; t.editSourceText(); nodes.get("#entry-text").value="广元午餐32元"; fetchImpl=async()=>{ throw Object.assign(new Error("网络不可用"),{offline:true}); }; await t.parseNaturalEntry(); assert.strictEqual(t.state.sourceEdit.active,true); assert.strictEqual(nodes.get("#entry-text").value,"广元午餐32元"); assert.strictEqual(t.state.parsed.recognized.amount,31);
  // 原句编辑中的草稿要同时记住“待识别新句”和回退所需旧原句/字段，刷新恢复后仍不能直接入账。
  await t.persistDraft(); const sourceDraft=t.currentDraft(); assert.strictEqual(sourceDraft.raw_text,"广元午餐32元"); assert.strictEqual(sourceDraft.source_edit.rawText,"广元午餐30元"); t.resetReview(); t.resumeDraft(sourceDraft); assert.strictEqual(t.state.sourceEdit.active,true); assert.strictEqual(nodes.get("#entry-text").value,"广元午餐32元"); assert.strictEqual(nodes.get("#review-card").classList.contains("hidden"),true); t.returnToReview(); assert.strictEqual(nodes.get("#entry-text").value,"广元午餐30元"); assert.strictEqual(nodes.get("#entry-form [name=amount]").value,31);
  // 返回确认页后，已在路上的解析响应也必须因 token 失效而不能覆盖人工确认。
  t.editSourceText(); nodes.get("#entry-text").value="广元午餐33元"; let resolveLate; fetchImpl=async()=>new Promise(resolve=>{ resolveLate=()=>resolve({ok:true,status:200,json:async()=>({raw_text:"广元午餐33元",recognized:{category:"meal",amount:33,item:"新结果"},missing:[],field_meta:{}})}); });
  const lateParse=t.parseNaturalEntry(); t.returnToReview(); resolveLate(); await lateParse; assert.strictEqual(t.state.parsed.recognized.amount,31); assert.strictEqual(nodes.get("#entry-text").value,"广元午餐30元");
  // 成功重识别后立即覆盖旧防抖草稿；刷新恢复的是新字段及同一首笔 client_id。
  t.editSourceText(); nodes.get("#entry-text").value="广元午餐34元"; fetchImpl=async()=>({ok:true,status:200,json:async()=>({raw_text:"广元午餐34元",recognized:{category:"meal",amount:34,item:"新米粉",occurred_at:"2026-09-15T12:00"},missing:[],field_meta:{}})}); await t.parseNaturalEntry(); const refreshed=t.currentDraft(); assert.strictEqual(refreshed.fields.amount,34); assert.strictEqual(refreshed.raw_text,"广元午餐34元"); assert.strictEqual(refreshed.record_client_ids[0],"reparse-new");
  t.state.savingEntry=true; assert.strictEqual(t.canReidentifySource(),false); t.editSourceText(); assert.strictEqual(t.state.sourceEdit,null); t.state.savingEntry=false;
  t.returnToReview(); t.state.reviewRecords=[t.state.parsed,{recognized:{category:"parking",amount:8}}]; assert.strictEqual(t.canReidentifySource(),false);

  // 新增与修改请求超时：必须写入同一 client_id 的待处理队列；其后同步重试
  // 用同一编号收敛，不能靠 toast 假装成功或重复入账。
  const timeoutError=()=>Object.assign(new Error("aborted"),{name:"AbortError"});
  resetStorage(); t.state.trip={id:7}; t.state.online=true;
  t.renderReview({raw_text:"兰州午餐30元",recognized:{category:"meal",amount:30,location:"兰州",item:"牛肉面",occurred_at:"2026-09-12T12:00"},missing:[],field_meta:{}},null,{recordClientIds:["timeout-new"]});
  fetchImpl=async()=>{ throw timeoutError(); }; await nodes.get("#entry-form").dispatch("submit");
  let uncertain=t.offlineQueue()[0]; assert.strictEqual(uncertain.client_id,"timeout-new"); assert.match(uncertain.error,/结果待核对/); assert.strictEqual(uncertain.payload.client_id,"timeout-new");
  fetchImpl=async(path,options)=>{ fetchCalls.push({path,options}); if(path==="/api/sync") return {ok:true,status:200,json:async()=>syncResult(options)}; if(path.startsWith("/api/dashboard")) return {ok:true,status:200,json:async()=>({total_spend:30,vehicle_cost:0,distance_km:0,fuel:{status:"insufficient_data",l_per_100km:null},vehicle_cost_per_km:null,by_category:{},entries:[]})}; return {ok:true,status:200,json:async()=>[]}; }; await t.syncOfflineQueue(); assert.strictEqual(t.offlineQueue().length,0); assert.strictEqual(JSON.parse(fetchCalls.find(call=>call.path==="/api/sync").options.body).client_id,"timeout-new");
  resetStorage(); t.state.trip={id:7}; t.state.online=true; const timedEdit={id:41,trip_id:7,category:"fuel",category_label:"油费",amount:360,location:"兰州",note:null,raw_text:"加油",occurred_at:"2026-09-12T13:00",fuel_grade:95,fuel_liters:45,fuel_unit_price:null,odometer:1200,full_tank:null,client_id:"timeout-edit",client_revision:4,version:4}; t.editEntry(timedEdit); nodes.get("#entry-form [name=amount]").value="361";
  fetchImpl=async()=>{ throw timeoutError(); }; await nodes.get("#entry-form").dispatch("submit"); uncertain=t.offlineQueue()[0]; assert.strictEqual(uncertain.client_id,"timeout-edit"); assert.match(uncertain.error,/结果待核对/); assert.strictEqual(uncertain.payload.client_revision,5);
  fetchImpl=async(path,options)=>{ fetchCalls.push({path,options}); if(path==="/api/sync") return {ok:true,status:200,json:async()=>syncResult(options)}; if(path.startsWith("/api/dashboard")) return {ok:true,status:200,json:async()=>({total_spend:361,vehicle_cost:361,distance_km:200,fuel:{status:"insufficient_data",l_per_100km:null},vehicle_cost_per_km:1.805,by_category:{},entries:[]})}; return {ok:true,status:200,json:async()=>[]}; }; await t.syncOfflineQueue(); assert.strictEqual(t.offlineQueue().length,0); assert.strictEqual(JSON.parse(fetchCalls.find(call=>call.path==="/api/sync").options.body).client_id,"timeout-edit");

  // 服务端回收站统一读取 records，同时展示消费和独立里程；在线删除里程后立即刷新。
  resetStorage(); t.state.trip={id:7}; t.state.online=true; const remoteEntry={id:11,trip_id:7,category:"meal",category_label:"餐饮",amount:30,location:"广元",note:"米粉",deleted_at:"2026-09-12T12:00:00",delete_expires_at:"2099-09-12T12:00:00"}; const remoteOdo={id:12,trip_id:7,odometer:33500,location:"大柴旦",record_type:"odometer_reading",deleted_at:"2026-09-12T13:00:00",delete_expires_at:"2099-09-12T13:00:00"};
  fetchImpl=async(path,options)=>{ fetchCalls.push({path,options}); if(path.startsWith("/api/trash")) return {ok:true,status:200,json:async()=>({entries:[remoteEntry],odometer_readings:[remoteOdo],records:[remoteEntry,remoteOdo]})}; if(path.startsWith("/api/odometer-readings/") && options?.method==="DELETE") return {ok:true,status:200,json:async()=>({deleted:true})}; return {ok:true,status:200,json:async()=>[]}; };
  await t.loadTrash(); assert.strictEqual(t.state.trash.length,2); assert.strictEqual(nodes.get("#trash-list").children.length,2); t.state.dashboard={odometer_readings:[{...remoteOdo}]}; await t.removeOdometer(remoteOdo); assert.ok(fetchCalls.some(call=>call.path==="/api/trash?trip_id=7"));

  // 无 client_id 的旧里程记录：在线修改/删除仍传 null，离线修改才生成稳定编号供服务器迁移绑定。
  resetStorage(); t.state.trip={id:7}; t.state.online=true; t.state.dashboard={odometer_readings:[]}; const legacyOdo={id:19,trip_id:7,odometer:1200,location:"张掖",note:null,client_id:null,client_revision:1,version:1}; let legacyPut=null;
  t.editOdometer(legacyOdo); fetchImpl=async(path,options)=>{ fetchCalls.push({path,options}); if(path==="/api/odometer-readings/19"){ legacyPut=JSON.parse(options.body); return {ok:true,status:200,json:async()=>({...legacyOdo,odometer:1210,client_revision:2})}; } if(path.startsWith("/api/dashboard")) return {ok:true,status:200,json:async()=>({total_spend:0,vehicle_cost:0,distance_km:210,fuel:{status:"ok",l_per_100km:null},vehicle_cost_per_km:null,by_category:{},entries:[]})}; if(path.startsWith("/api/odometer-readings?")) return {ok:true,status:200,json:async()=>[]}; throw new Error(path); }; await t.saveOdometer({trip_id:7,odometer:1210,source:"manual",client_id:"form-generated",client_revision:1}); assert.strictEqual(legacyPut.client_id,null); assert.strictEqual(legacyPut.client_revision,2);
  fetchCalls=[]; t.state.dashboard={odometer_readings:[legacyOdo]}; let legacyDelete=null; fetchImpl=async(path,options)=>{ fetchCalls.push({path,options}); if(path==="/api/odometer-readings/19"){ legacyDelete=JSON.parse(options.body); return {ok:true,status:200,json:async()=>({deleted:true})}; } if(path.startsWith("/api/trash")) return {ok:true,status:200,json:async()=>({records:[]})}; throw new Error(path); }; await t.removeOdometer(legacyOdo); assert.strictEqual(legacyDelete.client_id,null);
  resetStorage(); t.state.trip={id:7}; t.state.online=false; t.editOdometer(legacyOdo); await t.saveOdometer({trip_id:7,odometer:1220,source:"manual",client_id:"legacy-bound",client_revision:1}); const legacyQueued=t.offlineQueue()[0]; assert.strictEqual(legacyQueued.entity,"odometer"); assert.strictEqual(legacyQueued.op,"upsert"); assert.strictEqual(legacyQueued.client_id,"legacy-bound"); assert.strictEqual(legacyQueued.payload.id,19);

  // 在线恢复成功要在同一 Web Lock 中清理手机回收副本和旧队列，重新加载不能再出现。
  resetStorage(); t.state.trip={id:7}; t.state.online=true; const restoreLocal={entity:"entry",trip_id:7,client_id:"restore-persisted",record:{id:29,trip_id:7,client_id:"restore-persisted",client_revision:2,category:"meal",amount:30,deleted_at:"2026-09-15T10:00:00",delete_expires_at:"2099-09-15T10:00:00"},deleted_at:"2026-09-15T10:00:00",expires_at:"2099-09-15T10:00:00",server_delete_confirmed:true}; const oldRestoreQueue={entity:"entry",op:"restore",client_id:"restore-persisted",client_revision:3,payload:{id:29,trip_id:7,client_id:"restore-persisted",client_revision:3}}; putState({schema:26,revision:0,queue:[oldRestoreQueue],trash:[restoreLocal],draft:null}); t.state.trash=[restoreLocal];
  fetchImpl=async(path,options)=>{ fetchCalls.push({path,options}); if(path==="/api/entries/29/restore") return {ok:true,status:200,json:async()=>({id:29,trip_id:7,client_id:"restore-persisted",client_revision:4,deleted_at:null})}; if(path.startsWith("/api/dashboard")) return {ok:true,status:200,json:async()=>({total_spend:30,vehicle_cost:0,distance_km:0,fuel:{status:"ok",l_per_100km:null},vehicle_cost_per_km:null,by_category:{},entries:[]})}; if(path.startsWith("/api/odometer-readings?")) return {ok:true,status:200,json:async()=>[]}; if(path.startsWith("/api/trash?")) return {ok:true,status:200,json:async()=>({records:[]})}; throw new Error(path); };
  await t.restoreTrash(restoreLocal); assert.strictEqual(t.localState().trash.length,0); assert.strictEqual(t.offlineQueue().length,0); await t.loadTrash(); assert.strictEqual(t.state.trash.length,0);

  // 本地回收站保存完整 payload，严格按行程过滤并清理过期项。
  resetStorage(); t.state.trip={id:7}; t.state.online=false; const validTrash={entity:"entry",trip_id:7,client_id:"trash-a",record:{id:21,trip_id:7,category:"daily",amount:8,item:"矿泉水"},payload:{trip_id:7,raw_text:"买水8元",recognized:{category:"daily",amount:8,item:"矿泉水"}},raw_text:"买水8元",deleted_at:"2026-09-14T12:00:00",expires_at:"2099-09-14T12:00:00"}; const foreignTrash={...validTrash,client_id:"trash-b",trip_id:8,record:{...validTrash.record,trip_id:8},payload:{...validTrash.payload,trip_id:8}}; const expiredTrash={...validTrash,client_id:"trash-c",expires_at:"2020-01-01T00:00:00"}; putState({schema:26,revision:0,queue:[],trash:[validTrash,foreignTrash,expiredTrash],draft:null}); fetchImpl=async()=>{throw new Error("offline");}; await t.loadTrash(); assert.strictEqual(t.state.trash.length,1); assert.strictEqual(t.state.trash[0].client_id,"trash-a"); assert.strictEqual(nodes.get("#trash-list").children.length,1); const cleaned=JSON.parse(values.get("roadtrip.localState.v26")); assert.ok(cleaned.trash.some(item=>item.client_id==="trash-b")); assert.ok(!cleaned.trash.some(item=>item.client_id==="trash-c")); assert.strictEqual(t.localCsvRows().length,2);

  // 响应丢失场景：待同步 upsert 转同 client_id 的高版本 delete tombstone；撤销再转高版本 upsert/restore，绝不丢操作。
  resetStorage(); t.state.trip={id:7}; t.state.online=false; putState({schema:26,revision:0,queue:[entry("lost")],trash:[],draft:null}); let pending=t.offlineQueue()[0]; await t.removePending(pending); let afterDelete=t.offlineQueue()[0]; assert.strictEqual(afterDelete.op,"delete"); assert.strictEqual(afterDelete.client_id,"lost"); assert.strictEqual(afterDelete.client_revision,2); assert.ok(t.localState().trash[0].payload.recognized); assert.strictEqual(t.localState().trash[0].trip_id,1); await t.removePending(afterDelete); const afterUndo=t.offlineQueue()[0]; assert.ok(["upsert","restore"].includes(afterUndo.op)); assert.strictEqual(afterUndo.client_id,"lost"); assert.strictEqual(afterUndo.client_revision,3);

  // 待同步编辑沿用原 client_id，但内容变更必须升级 revision，以收敛旧版本已入服务端但响应丢失的情况。
  resetStorage(); t.state.trip={id:1}; putState({schema:26,revision:0,queue:[entry("pending-edit")],trash:[],draft:null}); const pendingEdit=t.offlineQueue()[0]; t.editPending(pendingEdit); assert.strictEqual(t.state.reviewQueueItem.client_id,"pending-edit"); assert.strictEqual(t.state.draftClientId,"pending-edit"); nodes.get("#entry-form [name=amount]").value="35"; await nodes.get("#entry-form").dispatch("submit"); const editedPending=t.offlineQueue()[0]; assert.strictEqual(editedPending.client_id,"pending-edit"); assert.strictEqual(editedPending.client_revision,2); assert.strictEqual(editedPending.payload.client_revision,2); assert.strictEqual(editedPending.payload.recognized.amount,35);

  // 普通已入账编辑沿用现有 client_id，发送 current+1，并且必须用服务端返回记录确认修改成功。
  resetStorage(); t.state.trip={id:7}; t.state.online=true; const serverEntry={id:31,trip_id:7,category:"meal",category_label:"餐饮",amount:30,location:"广元",note:"米粉",raw_text:"午餐",occurred_at:"2026-09-12T12:00:00",fuel_grade:null,fuel_liters:null,odometer:null,client_id:"server-cid",client_revision:4,version:4}; t.editEntry(serverEntry); nodes.get("#entry-form [name=amount]").value="31"; let editBody=null; fetchImpl=async(path,options)=>{ fetchCalls.push({path,options}); if(path==="/api/entries/31") { editBody=JSON.parse(options.body); return {ok:true,status:200,json:async()=>({...serverEntry,amount:31,client_revision:5,version:5})}; } if(path.startsWith("/api/dashboard")) return {ok:true,status:200,json:async()=>({total_spend:31,vehicle_cost:0,distance_km:0,fuel:{status:"ok",l_per_100km:null},vehicle_cost_per_km:null,by_category:{},entries:[]})}; return {ok:true,status:200,json:async()=>[]}; }; await nodes.get("#entry-form").dispatch("submit"); assert.strictEqual(editBody.client_id,"server-cid"); assert.strictEqual(editBody.client_revision,5); assert.strictEqual(editBody.recognized.amount,31);
  assert.throws(()=>t.verifyEntryResponse({...serverEntry,amount:30,client_revision:5,version:5},31,{category:"meal",amount:31,location:"广元",occurred_at:"2026-09-12T12:00:00",fuel_grade:null,fuel_liters:null,odometer:null,item:"米粉"},5,"午餐"),/未确认/);
  const minuteExpected={category:"meal",amount:31,location:"广元",occurred_at:"2026-09-12T12:00",fuel_grade:null,fuel_liters:null,odometer:null,item:"米粉"};
  assert.doesNotThrow(()=>t.verifyEntryResponse({...serverEntry,amount:31,occurred_at:"2026-09-12T12:00:00",client_revision:5},31,minuteExpected,5,"午餐"));
  assert.doesNotThrow(()=>t.verifyEntryResponse({...serverEntry,amount:31,occurred_at:"2026-09-12T12:00:37",client_revision:5},31,{...minuteExpected,occurred_at:"2026-09-12T12:00:37"},5,"午餐"));
  for(const [actual,expected] of [["2026-09-13T12:00:00","2026-09-12T12:00"],["2026-09-12T12:00:01","2026-09-12T12:00"],["2026-09-12T12:00:00Z","2026-09-12T12:00"],["2026-09-12T12:00:00+08:00","2026-09-12T12:00"],["0000-01-01T00:00","0000-01-01T00:00"],["not-a-time","not-a-time"],["",""]]) assert.throws(()=>t.verifyEntryResponse({...serverEntry,amount:31,occurred_at:actual,client_revision:5},31,{...minuteExpected,occurred_at:expected},5,"午餐"),/未确认/);
  const fuelReceipt={id:32,category:"fuel",amount:360,location:"兰州",occurred_at:"2026-09-12T13:00",fuel_grade:95,fuel_liters:45,fuel_unit_price:8.5,full_tank:1,odometer:1200,note:null,client_revision:2}; const fuelExpected={category:"fuel",amount:360,location:"兰州",occurred_at:"2026-09-12T13:00",fuel_grade:95,fuel_liters:45,fuel_unit_price:8.5,full_tank:true,odometer:1200,item:null}; assert.throws(()=>t.verifyEntryResponse({...fuelReceipt,fuel_unit_price:8.4},32,fuelExpected,2),/挂牌单价/); assert.throws(()=>t.verifyEntryResponse({...fuelReceipt,full_tank:0},32,fuelExpected,2),/是否加满/);

  // 行程信息面板同时修改标题与出发里程，标题在提交前去除首尾空格。
  resetStorage(); t.state.trip={id:7,name:"旧标题",origin:"成都",start_odometer:10000}; t.state.online=true;
  t.openTripEdit(); assert.strictEqual(nodes.get("#trip-edit-form [name=name]").value,"旧标题"); assert.strictEqual(nodes.get("#trip-edit-form [name=start_odometer]").value,10000);
  nodes.get("#trip-edit-form")._formData={name:"  青甘大环线  ",start_odometer:"9900"}; let tripEditBody=null;
  fetchImpl=async(path,options)=>{ fetchCalls.push({path,options}); if(path==="/api/trips/7"){ tripEditBody=JSON.parse(options.body); return {ok:true,status:200,json:async()=>({id:7,name:"青甘大环线",origin:"成都",start_odometer:9900})}; } if(path.startsWith("/api/dashboard")) return {ok:true,status:200,json:async()=>({total_spend:0,vehicle_cost:0,distance_km:0,fuel:{status:"ok",l_per_100km:null},vehicle_cost_per_km:null,by_category:{},entries:[]})}; if(path.startsWith("/api/odometer-readings")) return {ok:true,status:200,json:async()=>[]}; throw new Error(path); };
  await nodes.get("#trip-edit-form").dispatch("submit",{currentTarget:nodes.get("#trip-edit-form")}); assert.deepStrictEqual(tripEditBody,{name:"青甘大环线",start_odometer:9900,departure_date:null,planned_days:null}); assert.strictEqual(t.state.trip.name,"青甘大环线");

  // 无服务器 id 的本地项也必须联网；先同步 delete，服务器返回 tombstone 后才可清理手机数据。
  resetStorage(); t.state.trip={id:7}; t.state.online=false; const localOnly={entity:"entry",trip_id:7,client_id:"local-trash",local_only:true,record:{trip_id:7,category:"daily",amount:8,item:"水"},payload:{trip_id:7,raw_text:"买水8元",recognized:{category:"daily",amount:8,item:"水"}},deleted_at:"2026-09-15T10:00:00",expires_at:"2099-09-15T10:00:00"};
  const localDelete={...entry("local-trash",7),op:"delete",client_revision:2,payload:{trip_id:7,client_id:"local-trash",client_revision:2}};
  putState({schema:26,revision:0,queue:[localDelete],trash:[localOnly],draft:null}); t.state.trash=[localOnly]; await t.permanentlyDeleteTrash(localOnly); assert.strictEqual(t.localState().trash.length,1); assert.strictEqual(t.offlineQueue().length,1); assert.strictEqual(fetchCalls.length,0);
  t.state.online=true; fetchImpl=async(path,options)=>{ fetchCalls.push({path,options}); if(path==="/api/sync") return {ok:true,status:200,json:async()=>({status:"idempotent",entity:"entry",id:null,client_revision:2,tombstone:true})}; if(path==="/api/trash/purge-client") return {ok:true,status:200,json:async()=>({permanently_deleted:true,record_id:null,record_type:"entry",trip_id:7,client_id:"local-trash",client_revision:2})}; throw new Error(path); }; await t.permanentlyDeleteTrash(localOnly); assert.strictEqual(t.localState().trash.length,0); assert.strictEqual(t.offlineQueue().length,0); assert.deepStrictEqual(fetchCalls.map(call=>call.path),["/api/sync","/api/trash/purge-client"]); assert.deepStrictEqual(JSON.parse(fetchCalls[1].options.body),{entity:"entry",trip_id:7,client_id:"local-trash",client_revision:2});

  // 无 id 且没有匹配待删队列时不得请求服务器或清理本地。
  resetStorage(); t.state.trip={id:7}; t.state.online=true; putState({schema:26,revision:0,queue:[],trash:[localOnly],draft:null}); t.state.trash=[localOnly]; fetchImpl=async()=>{ throw new Error("不应请求服务器"); }; await t.permanentlyDeleteTrash(localOnly); assert.strictEqual(t.localState().trash.length,1); assert.strictEqual(fetchCalls.length,0);

  // 普通离线删除同步成功后，服务器 id 和确认状态必须写回回收站；队列清空后仍可单项永久删除。
  resetStorage(); t.state.trip={id:7}; t.state.online=true; const syncedTrash={...localOnly,client_id:"normal-delete"}; const normalDelete={...localDelete,client_id:"normal-delete",payload:{trip_id:7,client_id:"normal-delete",client_revision:2}};
  putState({schema:26,revision:0,queue:[normalDelete],trash:[syncedTrash],draft:null}); t.state.trash=[syncedTrash];
  fetchImpl=async(path,options)=>{ fetchCalls.push({path,options}); if(path==="/api/sync") return {ok:true,status:200,json:async()=>({status:"deleted",entity:"entry",id:61,client_revision:2,deleted_at:"2026-09-15T10:01:00",delete_expires_at:"2099-09-15T10:01:00",delete_confirmed:true})}; if(path.startsWith("/api/dashboard")) return {ok:true,status:200,json:async()=>({total_spend:0,vehicle_cost:0,distance_km:0,fuel:{status:"ok",l_per_100km:null},vehicle_cost_per_km:null,by_category:{},entries:[]})}; if(path.startsWith("/api/odometer-readings?")) return {ok:true,status:200,json:async()=>[]}; if(path==="/api/trash/entries/61") return {ok:true,status:200,json:async()=>({permanently_deleted:true,record_id:61,trip_id:7})}; throw new Error(path); };
  await t.syncOfflineQueue(); let syncedDoc=t.localState(); assert.strictEqual(syncedDoc.queue.length,0); assert.strictEqual(syncedDoc.trash.length,1); assert.strictEqual(syncedDoc.trash[0].record.id,61); assert.strictEqual(syncedDoc.trash[0].server_delete_confirmed,true);
  fetchCalls=[]; await t.permanentlyDeleteTrash(syncedDoc.trash[0]); assert.deepStrictEqual(fetchCalls.map(call=>call.path),["/api/trash/entries/61"]); assert.strictEqual(t.localState().trash.length,0);

  // 同步只得到无 id tombstone 时也要保留服务器确认，以便无队列时再建立不可逆墓碑。
  resetStorage(); t.state.trip={id:7}; t.state.online=true; const tombTrash={...localOnly,client_id:"confirmed-no-id"}, tombDelete={...localDelete,client_id:"confirmed-no-id",payload:{trip_id:7,client_id:"confirmed-no-id",client_revision:2}};
  putState({schema:26,revision:0,queue:[tombDelete],trash:[tombTrash],draft:null}); t.state.trash=[tombTrash];
  fetchImpl=async(path,options)=>{ fetchCalls.push({path,options}); if(path==="/api/sync") return {ok:true,status:200,json:async()=>({status:"idempotent",entity:"entry",id:null,client_revision:2,tombstone:true,delete_confirmed:true})}; if(path.startsWith("/api/dashboard")) return {ok:true,status:200,json:async()=>({total_spend:0,vehicle_cost:0,distance_km:0,fuel:{status:"ok",l_per_100km:null},vehicle_cost_per_km:null,by_category:{},entries:[]})}; if(path.startsWith("/api/odometer-readings?")) return {ok:true,status:200,json:async()=>[]}; if(path==="/api/trash/purge-client") return {ok:true,status:200,json:async()=>({permanently_deleted:true,record_id:null,record_type:"entry",trip_id:7,client_id:"confirmed-no-id",client_revision:2})}; throw new Error(path); };
  await t.syncOfflineQueue(); const confirmed=t.localState().trash[0]; assert.strictEqual(t.offlineQueue().length,0); assert.strictEqual(confirmed.server_delete_confirmed,true); assert.strictEqual(confirmed.record.id,undefined); fetchCalls=[]; await t.permanentlyDeleteTrash(confirmed); assert.deepStrictEqual(fetchCalls.map(call=>call.path),["/api/trash/purge-client"]); assert.strictEqual(t.localState().trash.length,0);

  // purge-client 发现服务器还是活动记录时，保留回收项并重建高一版的软删除，下次重试才永久删除。
  resetStorage(); t.state.trip={id:7}; t.state.online=true; const conflicted={...localOnly,client_id:"purge-conflict",server_delete_confirmed:true}; putState({schema:26,revision:0,queue:[],trash:[conflicted],draft:null}); t.state.trash=[conflicted];
  fetchImpl=async(path,options)=>{ fetchCalls.push({path,options}); if(path==="/api/trash/purge-client") return {ok:false,status:409,json:async()=>({error:"请先软删除",conflict:true,entity:"entry",record_id:71,trip_id:7,client_id:"purge-conflict",client_revision:4})}; throw new Error(path); };
  await t.permanentlyDeleteTrash(conflicted); let conflictDoc=t.localState(); assert.strictEqual(conflictDoc.trash.length,1); assert.strictEqual(conflictDoc.trash[0].record.id,71); assert.strictEqual(conflictDoc.trash[0].server_delete_confirmed,false); assert.strictEqual(conflictDoc.queue.length,1); assert.strictEqual(conflictDoc.queue[0].op,"delete"); assert.strictEqual(conflictDoc.queue[0].payload.id,71); assert.strictEqual(conflictDoc.queue[0].client_revision,5);
  fetchCalls=[]; fetchImpl=async(path,options)=>{ fetchCalls.push({path,options}); if(path==="/api/sync") return {ok:true,status:200,json:async()=>({status:"deleted",entity:"entry",id:71,client_revision:5,delete_confirmed:true})}; if(path==="/api/trash/entries/71") return {ok:true,status:200,json:async()=>({permanently_deleted:true,record_id:71,trip_id:7})}; throw new Error(path); };
  await t.permanentlyDeleteTrash(conflictDoc.trash[0]); assert.deepStrictEqual(fetchCalls.map(call=>call.path),["/api/sync","/api/trash/entries/71"]); assert.strictEqual(t.localState().trash.length,0); assert.strictEqual(t.offlineQueue().length,0);

  // 响应丢失场景：无 id 待删除项同步出服务器 id 后，紧接着执行永久删除再清本地。
  resetStorage(); t.state.trip={id:7}; t.state.online=true; const lostTrash={...localOnly,client_id:"lost-delete",record:{...localOnly.record},payload:{...localOnly.payload},local_only:true}; const lostDelete={...localDelete,client_id:"lost-delete",payload:{trip_id:7,client_id:"lost-delete",client_revision:2}}; putState({schema:26,revision:0,queue:[lostDelete],trash:[lostTrash],draft:null}); t.state.trash=[lostTrash];
  fetchImpl=async(path,options)=>{ fetchCalls.push({path,options}); if(path==="/api/sync") return {ok:true,status:200,json:async()=>({status:"deleted",entity:"entry",id:51,client_revision:2})}; if(path==="/api/trash/entries/51") return {ok:true,status:200,json:async()=>({permanently_deleted:true,record_id:51,record_type:"entry",trip_id:7,revisions_deleted:2})}; throw new Error(path); }; await t.permanentlyDeleteTrash(lostTrash); assert.deepStrictEqual(fetchCalls.map(call=>call.path),["/api/sync","/api/trash/entries/51"]); assert.strictEqual(t.localState().trash.length,0); assert.strictEqual(t.offlineQueue().length,0);

  // 有服务器 id 的回收站记录离线时不能假成功；联网后调用单项永久删除接口。
  resetStorage(); t.state.trip={id:7}; const remoteTrash={entity:"entry",trip_id:7,id:41,client_id:"remote-trash",category:"meal",category_label:"餐饮",amount:30,deleted_at:"2026-09-15T10:00:00",delete_expires_at:"2099-09-15T10:00:00"}; t.state.trash=[remoteTrash]; t.state.online=false; await t.permanentlyDeleteTrash(remoteTrash); assert.strictEqual(t.state.trash.length,1); assert.strictEqual(fetchCalls.length,0);
  t.state.online=true; fetchImpl=async(path,options)=>{ fetchCalls.push({path,options}); return {ok:true,status:200,json:async()=>({permanently_deleted:true,record_id:41,record_type:"entry",trip_id:7,revisions_deleted:2})}; }; await t.permanentlyDeleteTrash(remoteTrash); assert.strictEqual(fetchCalls[0].path,"/api/trash/entries/41"); assert.strictEqual(fetchCalls[0].options.method,"DELETE"); assert.deepStrictEqual(JSON.parse(fetchCalls[0].options.body),{trip_id:7,client_id:"remote-trash",client_revision:null}); assert.strictEqual(t.state.trash.length,0);

  // 相同 client_id 在其他实体或行程不是同一条记录，单删不得误清队列和回收站。
  resetStorage(); t.state.trip={id:7}; t.state.online=true; const target={...localOnly,client_id:"shared",entity:"entry"}, otherEntity={...localOnly,client_id:"shared",entity:"odometer",record:{trip_id:7,odometer:1234},payload:{trip_id:7,odometer:1234}}, otherTrip={...localOnly,client_id:"shared",trip_id:8,record:{trip_id:8,category:"daily",amount:9},payload:{trip_id:8}}; const targetDelete={...localDelete,client_id:"shared",entity:"entry",payload:{trip_id:7,client_id:"shared",client_revision:2}}, entityQueue={...targetDelete,entity:"odometer",payload:{trip_id:7,client_id:"shared",client_revision:2}}, tripQueue={...targetDelete,payload:{trip_id:8,client_id:"shared",client_revision:2}}; putState({schema:26,revision:0,queue:[targetDelete,entityQueue,tripQueue],trash:[target,otherEntity,otherTrip],draft:null}); t.state.trash=[target]; fetchImpl=async(path)=>path==="/api/sync" ? {ok:true,status:200,json:async()=>({status:"idempotent",entity:"entry",id:null,client_revision:2,tombstone:true})} : {ok:true,status:200,json:async()=>({permanently_deleted:true,record_type:"entry",record_id:null,trip_id:7,client_id:"shared",client_revision:2})}; await t.permanentlyDeleteTrash(target); const scoped=t.localState(); assert.strictEqual(scoped.queue.length,2); assert.strictEqual(scoped.trash.length,2); assert.ok(scoped.queue.some(item=>item.entity==="odometer")); assert.ok(scoped.queue.some(item=>item.payload.trip_id===8)); assert.ok(scoped.trash.some(item=>item.entity==="odometer")); assert.ok(scoped.trash.some(item=>item.trip_id===8));

  // 两个标签页同时点击时，Web Lock 使同步、墓碑和本地清理只执行一轮。
  resetStorage(); t.state.trip={id:7}; t.state.online=true; const raced={...localOnly,client_id:"raced"}, racedDelete={...localDelete,client_id:"raced",payload:{trip_id:7,client_id:"raced",client_revision:2}}; putState({schema:26,revision:0,queue:[racedDelete],trash:[raced],draft:null}); t.state.trash=[raced]; let raceSyncs=0,racePurges=0; fetchImpl=async(path)=>{ if(path==="/api/sync"){ raceSyncs++; return {ok:true,status:200,json:async()=>({status:"idempotent",entity:"entry",id:null,client_revision:2,tombstone:true})}; } racePurges++; return {ok:true,status:200,json:async()=>({permanently_deleted:true,record_type:"entry",record_id:null,trip_id:7,client_id:"raced",client_revision:2})}; }; await Promise.all([t.permanentlyDeleteTrash(raced),t.permanentlyDeleteTrash(raced)]); assert.strictEqual(raceSyncs,1); assert.strictEqual(racePurges,1); assert.strictEqual(t.localState().trash.length,0); assert.strictEqual(t.offlineQueue().length,0);

  // 远端和本地同一 entity + trip + client_id 的回收项只显示一条。
  resetStorage(); t.state.trip={id:7}; t.state.online=true; const localDuplicate={...localOnly,client_id:"dedup",record:{...localOnly.record,client_id:"dedup"}}, remoteDuplicate={id:88,trip_id:7,client_id:"dedup",category:"daily",category_label:"日常",amount:8,deleted_at:"2026-09-15T10:00:00",delete_expires_at:"2099-09-15T10:00:00"}; putState({schema:26,revision:0,queue:[],trash:[localDuplicate],draft:null});
  fetchImpl=async(path,options)=>{ fetchCalls.push({path,options}); return {ok:true,status:200,json:async()=>({records:[remoteDuplicate]})}; }; await t.loadTrash(); assert.strictEqual(t.state.trash.length,1); assert.strictEqual(t.state.trash[0].id,88);
  fetchCalls=[]; fetchImpl=async(path,options)=>{ fetchCalls.push({path,options}); return {ok:true,status:200,json:async()=>({permanently_deleted:true,record_id:88,trip_id:7})}; }; await t.permanentlyDeleteTrash(t.state.trash[0]); assert.strictEqual(fetchCalls[0].path,"/api/trash/entries/88"); assert.strictEqual(t.localState().trash.length,0); assert.strictEqual(t.state.trash.length,0);

console.log("frontend v31 regressions: OK");
})().catch(error=>{console.error(error);process.exitCode=1;});

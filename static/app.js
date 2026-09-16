const $ = (s) => document.querySelector(s);
const state = { trip: null, latestFinished: null, parsed: null, reviewRecords: [], reviewRecordClientIds: [], reviewIndex: 0, dashboard: null, editingEntryId: null, editingTargetVersion: null, reviewQueueItem: null, editingClientId: null, editingClientRevision: null, gps: null, online: false, draftClientId: null, sourceEdit: null, savingEntry: false, syncing: false, reviewSession: 0, localState: null, draftTimer: null, naturalParseTimer: null, naturalParseToken: 0, naturalParsePending: "", entryMode: "natural", batch: null, trash: [], days: [], view: "home", sheetFocus: null, sheetOverflow: null, sheetRestorePending: false };
const STORAGE = {trip:"roadtrip.cachedTrip",dashboard:"roadtrip.cachedDashboard",queue:"roadtrip.offlineQueue",localState:"roadtrip.localState.v26"};
const LOCAL_SCHEMA=31;
const RESET_KEY="roadtrip.resetEpoch";
let regionsPromise;
const categories = [
  ["fuel","油费"],["toll","ETC"],["parking","停车费"],["lodging","住宿"],
  ["meal","餐饮"],["ticket","门票娱乐"],["daily","旅行日用"],["transport","其他交通"],
  ["service","旅行服务"],["shopping","购物特产"],["clothing","衣物"],["vehicle","车辆费用"],["other","其他"]
];
const categoryLabels = Object.fromEntries(categories);
const itemExamples = {
  fuel:"95号汽油、98号汽油",
  toll:"成绵高速通行费、跨海大桥通行费",
  parking:"景区停车费、酒店停车费",
  lodging:"酒店房费、民宿、营地费",
  meal:"牛肉面、晚餐、咖啡",
  ticket:"景区门票、索道票、观光车票",
  daily:"一瓶啤酒、矿泉水、洗漱用品",
  transport:"打车、地铁票、轮渡",
  service:"旅游保险、行李寄存、手机流量",
  shopping:"当地特产、伴手礼、纪念品",
  clothing:"冲锋衣、帽子、鞋子",
  vehicle:"补胎、车辆维修、道路救援",
  other:"未归类杂费、临时支出"
};

function show(id, visible=true){ $(id).classList.toggle("hidden", !visible); }
function text(node, value){ node.textContent = value == null ? "—" : String(value); }
function num(value){ return value === "" || value == null ? null : Number(value); }
function money(value){ return value == null ? "—" : `¥${Number(value).toFixed(2)}`; }
function toast(message){ const box=$("#toast"); text(box,message); box.classList.add("show"); setTimeout(()=>box.classList.remove("show"),2600); }
function localDateTimeValue(date=new Date()){
  const pad=value=>String(value).padStart(2,"0");
  return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
function localDateKey(date=new Date()){
  const pad=value=>String(value).padStart(2,"0");
  return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}`;
}
function displayEntryTime(value){ return value ? String(value).replace("T"," ").slice(0,16) : null; }
function readStored(key,fallback=null){ try{ const value=localStorage.getItem(key); return value ? JSON.parse(value) : fallback; }catch(_){ return fallback; } }
function writeStored(key,value){
  try{ localStorage.setItem(key,JSON.stringify(value)); }
  catch(error){ const failure=new Error("手机本地存储空间不足，记录尚未保存；请释放空间后重试"); failure.storage=true; throw failure; }
}
let offlineQueueReadError=null;
function clone(value){ return value == null ? value : JSON.parse(JSON.stringify(value)); }
function blankLocalState(){ return {schema:LOCAL_SCHEMA,revision:0,queue:[],trash:[],draft:null}; }
function normalizeQueueItem(item){
  const source=item && item.payload ? item : {payload:item};
  const payload=clone(source.payload || {});
  if(source.id != null && payload.id == null) payload.id=source.id;
  const entity=source.entity || (payload.odometer != null && payload.category == null ? "odometer" : "entry");
  const op=source.op || "upsert";
  const clientId=source.client_id || payload.client_id || newClientId();
  payload.client_id=clientId;
  return {entity,op,client_id:clientId,payload,client_revision:Number(source.client_revision || payload.client_revision || 1),queued_at:source.queued_at || new Date().toISOString(),updated_at:source.updated_at || source.queued_at || new Date().toISOString(),error:source.error || null};
}
function readV26(){
  try{
    const raw=localStorage.getItem(STORAGE.localState); if(!raw) return null;
    const doc=JSON.parse(raw); if(!doc || ![26,LOCAL_SCHEMA].includes(Number(doc.schema)) || !Array.isArray(doc.queue)) throw new Error("localState格式无效");
    offlineQueueReadError=null; return {...blankLocalState(),...doc,queue:doc.queue.map(normalizeQueueItem),trash:Array.isArray(doc.trash)?doc.trash:[]};
  }catch(_){ offlineQueueReadError=new Error("无法读取手机离线记录，已阻止覆盖；请先导出或修复浏览器数据"); return null; }
}
function legacyQueue(){
  try{
    const raw=localStorage.getItem(STORAGE.queue); if(!raw) return [];
    const value=JSON.parse(raw); if(!Array.isArray(value)) throw new Error("队列格式无效");
    return value.map(normalizeQueueItem);
  }catch(_){ offlineQueueReadError=new Error("无法读取旧版离线记录，已阻止覆盖；请先导出或修复浏览器数据"); return []; }
}
function ensureLocalStateLocked(){
  let doc=readV26();
  if(doc) return doc;
  if(offlineQueueReadError) throw offlineQueueReadError;
  doc=blankLocalState();
  // 旧队列只迁移一次。保留旧key，待 v26 成功运行后再由用户清理。
  const old=legacyQueue(); if(old.length) doc.queue=old;
  writeStored(STORAGE.localState,doc);
  const verified=readV26();
  if(!verified || verified.revision!==doc.revision) throw new Error("手机本地状态校验失败，记录未覆盖");
  return verified;
}
function localState(){ return state.localState || readV26() || { ...blankLocalState(), queue:legacyQueue() }; }
function offlineQueue(){ const doc=localState(); return Array.isArray(doc.queue) ? doc.queue : []; }
function queueForWrite(){ const queue=offlineQueue(); if(offlineQueueReadError) throw offlineQueueReadError; return queue; }
async function mutateLocalState(mutator){
  return withOfflineQueueLock(async()=>{
    const current=ensureLocalStateLocked(), next=clone(current), result=await mutator(next,current);
    next.schema=LOCAL_SCHEMA; next.revision=Number(current.revision||0)+1;
    writeStored(STORAGE.localState,next);
    const verified=readV26();
    if(!verified || verified.revision!==next.revision) throw new Error("手机本地状态写入校验失败，记录仍保留在当前页面");
    state.localState=verified; renderPending(); setOnline(state.online);
    return result;
  });
}
function saveOfflineQueue(queue){ return mutateLocalState(doc=>{ doc.queue=queue.map(normalizeQueueItem); }); }
async function withOfflineQueueLock(work){
  if(!navigator.locks?.request) throw new Error("当前浏览器不支持安全的多标签离线记录；请升级浏览器后重试");
  return navigator.locks.request("roadtrip.localState.v26", {mode:"exclusive"}, work);
}
function newClientId(){ return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`; }
function applyServerReset(epoch){
  if(!epoch || localStorage.getItem(RESET_KEY)===epoch) return;
  Object.values(STORAGE).forEach(key=>localStorage.removeItem(key));
  localStorage.setItem(RESET_KEY,epoch);
  state.trip=null; state.latestFinished=null; state.dashboard=null; state.parsed=null; state.sourceEdit=null; state.gps=null;
}
async function nearestChineseRegion(latitude,longitude){
  regionsPromise ||= fetch("/china_regions.json").then(response=>response.json());
  const regions=await regionsPromise;
  const radians=Math.PI/180, cosLat=Math.cos(latitude*radians);
  let best=null, bestDistance=Infinity;
  regions.forEach(region=>{
    const dx=(region.lon-longitude)*cosLat, dy=region.lat-latitude, distance=dx*dx+dy*dy;
    if(distance<bestDistance){ best=region; bestDistance=distance; }
  });
  return best ? `${best.p} · ${best.c} · ${best.n}附近` : "中国境内（大致位置未知）";
}

async function api(path, options={}){
  let response;
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),12000);
  try{ response = await fetch(path, {headers:{"Content-Type":"application/json"},signal:controller.signal,...options}); }
  catch(cause){
    if(cause?.name==="AbortError"){
      const error=new Error("请求超时，服务器是否已保存尚不确定；请勿重复提交，恢复连接后先刷新或查看待处理记录");
      error.uncertain=true; throw error;
    }
    setOnline(false); const error=new Error("当前无网络，已切换为离线记录模式"); error.offline=true; throw error;
  }
  finally{ clearTimeout(timer); }
  let body = {};
  try { body = await response.json(); } catch (_) {}
  if(!response.ok){ const error=new Error(body.error || `请求失败（${response.status}）`); error.status=response.status; error.body=body; throw error; }
  return body;
}

function setOnline(ok){
  state.online=ok;
  const badge=$("#connection");
  const pending=offlineQueue().length;
  text(badge, ok ? (pending ? `已连接 · 待处理 ${pending}` : "已连接") : (pending ? `离线 · 待同步 ${pending}` : "离线可记账"));
  badge.style.background = ok ? "#dceee6" : "#fff0f0";
}

// V3.1 retains the proven local-first data flow. Layout changes never mutate
// the active page, Day, draft, focus or pending-save state.
function setScreen(name){
  const target=["home","trip","bills","stats"].includes(name) ? name : "home";
  if(target!=="home" && !state.trip){ toast("开始一段行程后即可查看这里"); name="home"; }
  else name=target;
  state.view=name;
  document.querySelectorAll("[data-screen]").forEach(node=>node.classList.toggle("active",node.dataset.screen===name));
  document.querySelectorAll(".nav-item[data-go-screen]").forEach(node=>node.classList.toggle("active",node.dataset.goScreen===name));
  const main=$("#app-main"); if(main) main.dataset.screen=name;
  window.scrollTo?.({top:0,behavior:"smooth"});
}
function setSheetOpen(id,open){
  const sheet=$(`#${id}-sheet`), backdrop=$("#sheet-backdrop");
  if(open) syncVisualViewport();
  if(open && !state.sheetRestorePending){
    state.sheetFocus=document.activeElement || null;
    state.sheetOverflow=document.body?.style?.overflow ?? "";
    state.sheetRestorePending=true;
    if(document.body?.style) document.body.style.overflow="hidden";
  }
  if(sheet) sheet.classList.toggle("open",open);
  if(backdrop) backdrop.classList.toggle("open",open);
  if(sheet){
    sheet.setAttribute?.("aria-hidden",String(!open));
    // 关闭的 sheet 即使仍因过渡留在 DOM，也不能落入原生 Tab 顺序。
    if(open){ sheet.removeAttribute?.("inert"); if("inert" in sheet) sheet.inert=false; }
    else { sheet.setAttribute?.("inert",""); if("inert" in sheet) sheet.inert=true; }
  }
  // 弹层打开后把焦点带进来。这样键盘用户不会继续在被遮住的页面上操作，
  // 也为 Tab 循环提供一个可靠的起点。
  if(open && sheet){
    const first=sheet.querySelector?.("button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href]");
    (first || sheet).focus?.();
  }
  const anyOpen=["record","odometer"].some(name=>$(`#${name}-sheet`)?.classList?.contains?.("open"));
  if(!anyOpen && state.sheetRestorePending){
    if(document.body?.style) document.body.style.overflow=state.sheetOverflow || "";
    const restore=state.sheetFocus; state.sheetFocus=null; state.sheetOverflow=null; state.sheetRestorePending=false;
    restore?.focus?.();
  }
}
function openSheet(){
  return ["record","odometer"].map(name=>$(`#${name}-sheet`)).find(sheet=>sheet?.classList?.contains?.("open")) || null;
}
function sheetFocusable(sheet){
  return [...(sheet?.querySelectorAll?.("button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href]") || [])]
    .filter(node=>{
      if(node.hidden || node.tabIndex===-1) return false;
      // hidden 属性可能挂在 review-card 等祖先上；getClientRects 为 0 能同时
      // 排除祖先 display:none、visibility 折叠和已移出布局的控件。
      if(!node.getClientRects?.().length) return false;
      const style=window.getComputedStyle?.(node);
      return style?.display!=="none" && style?.visibility!=="hidden" && style?.visibility!=="collapse";
    });
}
function trapSheetFocus(event){
  if(event.key!=="Tab") return;
  const sheet=openSheet(); if(!sheet) return;
  const focusable=sheetFocusable(sheet);
  if(!focusable.length){ event.preventDefault?.(); sheet.focus?.(); return; }
  const active=document.activeElement, first=focusable[0], last=focusable[focusable.length-1];
  if(event.shiftKey ? (active===first || !sheet.contains?.(active)) : (active===last || !sheet.contains?.(active))){
    event.preventDefault?.(); (event.shiftKey ? last : first).focus?.();
  }
}
function cancelNaturalParse(){
  clearTimeout(state.naturalParseTimer); state.naturalParseTimer=null;
  state.naturalParseToken++; state.naturalParsePending="";
  const button=$("#parse-entry"); if(button){ button.disabled=false; text(button,"识别并确认"); }
}
function clearOdometerEdit({reset=false}={}){
  editingOdometerId=null; editingOdometerRevision=1; editingOdometerClientId=null;
  const form=$("#odometer-form");
  if(reset){
    form?.reset?.();
    ["odometer","occurred_at","location","note"].forEach(name=>{ const control=$(`#odometer-form [name=${name}]`); if(control) control.value=""; });
  }
  const submit=form?.querySelector?.("button[type=submit]"); if(submit) text(submit,"保存里程");
}
function closeSheets(){
  // 关闭消费弹层是“暂存并退出”，不是丢弃；尤其 Escape 不能让刚输入、
  // 还未来得及跑 300ms 防抖的草稿消失。
  if($("#record-sheet")?.classList?.contains?.("open") && (state.parsed || $("#entry-text")?.value?.trim?.())){
    persistDraft().catch(error=>toast(error.message));
  }
  cancelNaturalParse(); setSheetOpen("record",false); setSheetOpen("odometer",false);
  // 关闭不清消费草稿；里程编辑态不能泄漏到下一次“新增”。
  clearOdometerEdit();
}
function openRecordSheet(){
  if(!state.trip){ toast("请先开始一段行程"); return; }
  setSheetOpen("odometer",false); setSheetOpen("record",true);
  if(!state.parsed){ show("#entry-card",true); show("#review-card",false); }
}
function setEntryMode(mode,focus=false){
  const next=mode==="manual" ? "manual" : "natural";
  if(next!=="natural") cancelNaturalParse();
  state.entryMode=next;
  const natural=next==="natural";
  show("#natural-entry-panel",natural); show("#manual-entry-panel",!natural);
  [["#entry-mode-natural",natural],["#entry-mode-manual",!natural]].forEach(([selector,active])=>{
    const button=$(selector); if(!button) return;
    button.classList.toggle("active",active); button.setAttribute?.("aria-selected",String(active));
  });
  if(focus){ const target=natural ? $("#entry-text") : document.querySelector(".quick-category"); target?.focus?.(); }
}
async function parseNaturalEntry({silent=false,token=null,rawText=null}={}){
  const raw=String(rawText ?? ($("#entry-text")?.value || "")).trim();
  if(!raw){ if(!silent) toast("先说一句刚刚发生了什么"); return; }
  // 从确认页回来重新识别时，先保护用户已经逐项改过的内容。自动识别
  // 不弹确认框，避免输入原文时被打断；用户明确点按钮才可替换。
  if(state.sourceEdit?.active && state.savingEntry) return;
  if(state.sourceEdit?.active && state.sourceEdit.manualChanges){
    if(silent) return;
    if(!confirm("重新识别会替换当前确认内容；你手动修改的字段将由新结果覆盖。是否继续？")) return;
  }
  const requestToken=token==null ? ++state.naturalParseToken : token;
  if(requestToken!==state.naturalParseToken) return;
  state.naturalParsePending=raw;
  const button=$("#parse-entry"); if(button) { button.disabled=true; text(button,"正在识别…"); }
  try{
    const parsed=await api("/api/parse",{method:"POST",body:JSON.stringify({text:raw})});
    // 只让最后一段文字的响应进入确认页；旧请求即使晚返回也不会覆盖新内容或手动填写。
    if(requestToken!==state.naturalParseToken || state.entryMode!=="natural" || String($("#entry-text")?.value || "").trim()!==raw) return;
    state.naturalParsePending="";
    // 普通新增的重新识别沿用同一 client_id，不能变成第二笔待提交记录。
    const reidentify=state.sourceEdit?.active ? state.sourceEdit : null;
    const responseRecords=Array.isArray(parsed.records) && parsed.records.length ? parsed.records : [parsed];
    const recordClientIds=reidentify ? [reidentify.clientId,...responseRecords.slice(1).map(()=>newClientId())] : null;
    renderReview(parsed,null,reidentify ? {recordClientIds} : {});
    state.sourceEdit=null;
    // 自动识别的输入防抖草稿可能已先写入旧确认内容；成功进入新确认页后
    // 立即覆盖为新字段和固定 client_id，刷新也不会回到旧结果。
    await persistDraft();
  }catch(error){
    if(requestToken===state.naturalParseToken && !silent) toast(error.offline ? "离线时可切换到手动填写；原文字已经保留" : error.message);
  }finally{
    if(requestToken===state.naturalParseToken){
      if(state.naturalParsePending===raw) state.naturalParsePending="";
      if(button){ button.disabled=false; text(button,"识别并确认"); }
    }
  }
}
function scheduleNaturalParse(){
  clearTimeout(state.naturalParseTimer);
  const raw=String($("#entry-text")?.value || "").trim();
  // 新输入会使上一请求失效；无论新文本长短，都要立刻还原按钮，
  // 让用户可以手动识别简短文字而不会被旧请求的 busy 状态锁住。
  const button=$("#parse-entry"); if(button){ button.disabled=false; text(button,"识别并确认"); }
  const token=++state.naturalParseToken; state.naturalParsePending=raw;
  // 避免每输入一个字就请求；停顿后自动进入核对页，按钮仍可立即触发。
  if(raw.length<6){ state.naturalParsePending=""; return; }
  state.naturalParseTimer=setTimeout(()=>parseNaturalEntry({silent:true,token,rawText:raw}),700);
}
function openOdometerSheet(){
  if(!state.trip){ toast("请先开始一段行程"); return; }
  clearOdometerEdit({reset:true});
  setSheetOpen("record",false); setSheetOpen("odometer",true);
  const control=$("#odometer-form [name=occurred_at]"); if(control && !control.value) control.value=localDateTimeValue();
  $("#odometer-form [name=odometer]")?.focus?.();
}

async function bootstrap(){
  try{
    const odometerTime=$("#odometer-form [name=occurred_at]"); if(odometerTime && !odometerTime.value) odometerTime.value=localDateTimeValue();
    await withOfflineQueueLock(async()=>{ state.localState=ensureLocalStateLocked(); });
    const health=await api("/api/health"); applyServerReset(health.reset_epoch); setOnline(true);
    const trips=await api("/api/trips");
    state.trip=trips.find(t=>t.status==="active") || null;
    state.latestFinished=state.trip ? null : (trips.find(t=>t.status==="finished") || null);
    writeStored(STORAGE.trip,state.trip);
    renderTrip();
    if(state.trip) await loadDashboard();
    await syncOfflineQueue();
    await loadTrash();
  }catch(error){
    setOnline(false); state.trip=readStored(STORAGE.trip,null); state.dashboard=readStored(STORAGE.dashboard,null);
    renderTrip(); if(state.trip && state.dashboard) renderDashboard(); renderPending();
    toast(state.trip ? "当前无网络，新增消费会先保存在手机" : error.message);
  }
}

function renderTrip(){
  const active=Boolean(state.trip);
  show("#trip-empty",!active); show("#trip-active",active); show("#entry-card",active);
  show("#finish-card",active); show("#history-card",active); show("#odometer-card",active); show("#trash-card",active); show("#home-today",active); show("#home-recent-card",active); if(!active) show("#review-card",false);
  show("#restore-trip",!active && Boolean(state.latestFinished));
  if(!active && state.latestFinished) text($("#restore-trip-name"),state.latestFinished.name);
  if(!active) closeTripEdit();
  if(!active) return;
  text($("#trip-name"),state.trip.name);
  text($("#trip-route"),`${state.trip.origin}${state.trip.destination ? ` → ${state.trip.destination}` : ""} · 出发里程 ${state.trip.start_odometer} km`);
  text($("#trip-summary-text"),`${state.trip.origin}${state.trip.destination ? ` → ${state.trip.destination}` : ""} · 出发里程 ${state.trip.start_odometer} km`);
  renderOdometers(); renderTrash(); renderDraftBanner(); updateFinishControl();
}

function finishMinimumOdometer(){
  // 服务端 finish_trip 以 all_odometer_points 的最大值为下限，而不是按
  // 时间排序后的 current_odometer；这里同时扫描 dashboard 的两类原始点，
  // 防止补录较早时间但更高的里程时，确认按钮错误地提前可点。
  const dashboard=state.dashboard || {};
  const points=[state.trip?.start_odometer,dashboard.latest_odometer,dashboard.current_odometer,
    ...(Array.isArray(dashboard.entries) ? dashboard.entries.map(entry=>entry?.odometer) : []),
    ...(Array.isArray(dashboard.odometer_readings) ? dashboard.odometer_readings.map(reading=>reading?.odometer) : [])]
    .map(num).filter(Number.isFinite);
  return points.length ? Math.max(...points) : null;
}
function finishRequirements(){
  const form=$("#finish-form"), end=num(new FormData(form).get("end_odometer"));
  const confirmed=Boolean(form?.querySelector?.("[name=confirm_finish]")?.checked);
  const minimum=finishMinimumOdometer();
  return {valid:Number.isFinite(end) && end>=0 && (minimum===null || end>=minimum),minimum,confirmed,pending:offlineQueue().length};
}
function updateFinishControl(){
  const form=$("#finish-form"), button=form?.querySelector?.("button[type=submit]");
  if(!button) return;
  const requirements=finishRequirements();
  button.disabled=!(requirements.valid && requirements.confirmed && requirements.pending===0);
}

function currentTripId(){ return state.trip?.id == null ? null : Number(state.trip.id); }
function valueTripId(value){
  if(value == null || value === "") return null;
  const parsed=Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}
function trashTripId(item){
  const record=item?.record || {};
  const payload=item?.payload || {};
  return valueTripId(item?.trip_id ?? record.trip_id ?? payload.trip_id ?? record.payload?.trip_id);
}
function trashExpiresAt(item){
  const record=item?.record || {}, payload=item?.payload || {};
  return item?.expires_at || item?.delete_expires_at || record.expires_at || record.delete_expires_at || payload.expires_at || payload.delete_expires_at || null;
}
function trashExpired(item, now=Date.now()){
  const expires=trashExpiresAt(item); return Boolean(expires && Date.parse(expires) <= now);
}
function localTrashEntry({entity,record,payload,clientId,tripId,rawText}={}){
  const fullRecord=clone(record || payload || {}), fullPayload=clone(payload || record || {});
  const resolvedTrip=valueTripId(tripId ?? fullRecord.trip_id ?? fullPayload.trip_id ?? currentTripId());
  const deletedAt=new Date().toISOString();
  return {entity:entity || "entry",trip_id:resolvedTrip,client_id:clientId || fullRecord.client_id || fullPayload.client_id || null,
    record:fullRecord,payload:fullPayload,raw_text:rawText ?? fullPayload.raw_text ?? fullRecord.raw_text ?? "",
    deleted_at:deletedAt,expires_at:new Date(Date.now()+30*24*60*60*1000).toISOString(),local_only:fullRecord.id == null};
}
function isCurrentTripTrash(item){ return currentTripId()!=null && trashTripId(item)===currentTripId() && !trashExpired(item); }
function odometerTrash(item){
  const record=trashRecord(item);
  return item?.entity==="odometer" || record?.record_type==="odometer_reading" || (record?.odometer != null && record?.category == null);
}
function trashEntity(item){ return odometerTrash(item) ? "odometer" : "entry"; }
function trashKey(item){
  const record=trashRecord(item), entity=trashEntity(item), id=record?.id ?? item?.id;
  const tripId=trashTripId(item), clientId=item?.client_id || record?.client_id;
  return `${entity}:${tripId}:${clientId ? `client:${clientId}` : id != null ? `id:${id}` : "unknown"}`;
}
function currentDraft(){
  const draft=localState().draft;
  return draft && Number(draft.trip_id)===currentTripId() ? draft : null;
}
function draftSummary(draft){
  const fields=draft.fields || draft.recognized || {};
  return [categoryLabels[fields.category] || "待确认消费",fields.amount != null ? money(fields.amount) : "金额待补充",fields.location,fields.item || fields.note].filter(Boolean).join(" · ");
}
function captureActiveReview(){
  if(!state.parsed || !state.reviewRecords.length || state.reviewIndex>=state.reviewRecords.length) return;
  const f=new FormData($("#entry-form")), current=state.parsed, clientId=state.reviewRecordClientIds?.[state.reviewIndex] || current.client_id || state.draftClientId || newClientId();
  current.client_id=clientId;
  current.recognized={...(current.recognized||{}),category:f.get("category")||null,amount:num(f.get("amount")),location:f.get("location")||null,item:f.get("item")||null,occurred_at:f.get("occurred_at")||null,fuel_grade:num(f.get("fuel_grade")),fuel_liters:num(f.get("fuel_liters")),fuel_unit_price:num(f.get("fuel_unit_price")),odometer:num(f.get("odometer")),full_tank:f.get("full_tank")===""?null:f.get("full_tank")==="true"};
  current.field_meta=clone(current.field_meta || {});
  state.reviewRecords[state.reviewIndex]=current;
  state.reviewRecordClientIds ||= [];
  state.reviewRecordClientIds[state.reviewIndex]=clientId;
}
function renderDraftBanner(){
  const box=$("#draft-notice"); if(!box) return;
  const draft=currentDraft(); box.replaceChildren(); show("#draft-notice",Boolean(draft)); if(!draft) return;
  const label=document.createElement("span"); text(label,`有一条未完成记录：${draftSummary(draft)}`);
  const actions=document.createElement("span"); actions.className="draft-actions";
  const resume=document.createElement("button"); resume.type="button"; resume.className="history-button"; text(resume,"继续填写"); resume.addEventListener("click",()=>resumeDraft(draft));
  const discard=document.createElement("button"); discard.type="button"; discard.className="history-button danger"; text(discard,"丢弃草稿"); discard.addEventListener("click",()=>discardDraft());
  actions.append(resume,discard); box.append(label,actions);
}
function draftFromForm(){
  if(!state.trip) return null;
  const f=new FormData($("#entry-form"));
  const active=state.parsed || {recognized:{},missing:[],field_meta:{},raw_text:""};
  const clientId=state.reviewQueueItem?.client_id || state.editingClientId || state.draftClientId || newClientId();
  const clientRevision=Number(state.reviewQueueItem?.client_revision || state.editingClientRevision || 1);
  if(state.parsed) captureActiveReview();
  const records=state.reviewRecords.length ? clone(state.reviewRecords) : [];
  return {trip_id:currentTripId(),kind:state.reviewQueueItem ? "pending-edit" : state.editingEntryId==null ? "new" : "server-edit",target_id:state.editingEntryId || state.reviewQueueItem?.client_id || null,target_version:state.editingTargetVersion || active.recognized?.version || null,client_id:clientId,client_revision:clientRevision,raw_text:$("#entry-text").value || active.raw_text || "",fields:{category:f.get("category")||null,amount:num(f.get("amount")),location:f.get("location")||null,item:f.get("item")||null,occurred_at:f.get("occurred_at")||null,fuel_grade:num(f.get("fuel_grade")),fuel_liters:num(f.get("fuel_liters")),fuel_unit_price:num(f.get("fuel_unit_price")),odometer:num(f.get("odometer")),full_tank:f.get("full_tank")===""?null:f.get("full_tank")==="true"},gps:state.gps ? clone(state.gps) : null,parse_hints:clone(active.missing || []),field_meta:clone(active.field_meta || {}),records,record_index:state.reviewIndex,record_client_ids:clone(state.reviewRecordClientIds || []),multiple_remaining:clone(records.slice(state.reviewIndex+1)),source_edit:state.sourceEdit?.active ? clone(state.sourceEdit) : null,updated_at:new Date().toISOString()};
}
function scheduleDraftSave(){
  if(!state.trip || (!state.parsed && !$("#entry-text").value.trim())) return;
  clearTimeout(state.draftTimer); state.draftTimer=setTimeout(()=>persistDraft().catch(error=>toast(error.message)),300);
}
async function persistDraft(){
  const draft=draftFromForm(); if(!draft) return;
  await mutateLocalState(doc=>{ doc.draft=draft; });
}
async function discardDraft(){
  clearTimeout(state.draftTimer);
  const tripId=currentTripId();
  await mutateLocalState(doc=>{ if(doc.draft && Number(doc.draft.trip_id)===tripId) doc.draft=null; });
  if(state.parsed) resetReview(true); renderDraftBanner(); toast("草稿已丢弃");
}
function resumeDraft(draft){
  if(!draft || Number(draft.trip_id)!==currentTripId()) return;
  const fields=draft.fields || draft.recognized || {};
  state.reviewQueueItem=draft.kind==="pending-edit" ? offlineQueue().find(item=>item.client_id===draft.client_id) || null : null;
  $("#entry-text").value=draft.raw_text || "";
  const parsed={raw_text:draft.raw_text || "",recognized:{...fields,...(draft.gps || {}),client_id:draft.client_id,client_revision:draft.client_revision,version:draft.target_version},missing:draft.parse_hints || [],field_meta:draft.field_meta || {},derived_fields:[],can_save:false};
  if(Array.isArray(draft.records) && draft.records.length) parsed.records=draft.records;
  renderReview(parsed,draft.kind==="server-edit" ? draft.target_id : null,{clientId:draft.client_id || null,clientRevision:draft.client_revision,targetVersion:draft.target_version,queueItem:state.reviewQueueItem,recordIndex:Number(draft.record_index||0),recordClientIds:draft.record_client_ids});
  if(draft.source_edit?.active && canReidentifySource()) activateSourceEdit(draft.source_edit,draft.raw_text || draft.source_edit.rawText || "");
  renderDraftBanner();
}
function updateDraftFromEvent(){ scheduleDraftSave(); }

function odometerRows(){
  const rows=state.dashboard?.odometer_readings || state.dashboard?.odometers || [];
  return Array.isArray(rows) ? rows : [];
}
function renderOdometers(){
  const box=$("#odometer-history"); if(!box) return; box.replaceChildren();
  const rows=odometerRows().slice().sort((a,b)=>String(b.occurred_at||"").localeCompare(String(a.occurred_at||"")));
  if(!rows.length){ const empty=document.createElement("p"); empty.className="empty"; text(empty,"还没有独立里程记录"); box.append(empty); return; }
  rows.slice(0,8).forEach(row=>{
    const item=document.createElement("div"); item.className="history-item";
    const left=document.createElement("div"), title=document.createElement("b"), detail=document.createElement("small"), actions=document.createElement("div");
    text(title,`${row.odometer} km`); text(detail,[displayEntryTime(row.occurred_at),row.location,row.note].filter(Boolean).join(" · "));
    actions.className="history-actions";
    const edit=document.createElement("button"); edit.type="button"; edit.className="history-button"; text(edit,"修改"); edit.addEventListener("click",()=>editOdometer(row));
    const remove=document.createElement("button"); remove.type="button"; remove.className="history-button danger"; text(remove,"删除"); remove.addEventListener("click",()=>removeOdometer(row));
    actions.append(edit,remove); left.append(title,detail); item.append(left,actions); box.append(item);
  });
}
let editingOdometerId=null, editingOdometerRevision=1, editingOdometerClientId=null;
function editOdometer(row){
  editingOdometerId=row.id || null; editingOdometerRevision=Number(row.client_revision||row.version||1)+1; editingOdometerClientId=row.client_id || null; const form=$("#odometer-form"); form._formData={odometer:row.odometer,occurred_at:row.occurred_at||localDateTimeValue(),location:row.location||"",note:row.note||""};
  ["odometer","occurred_at","location","note"].forEach(name=>{ const control=$(`#odometer-form [name=${name}]`); if(control) control.value=form._formData[name]; });
  const submit=form.querySelector("button[type=submit]"); if(submit) text(submit,"保存里程修改");
  // 编辑入口必须立即可见，不能只在后台填充表单。
  setSheetOpen("record",false); setSheetOpen("odometer",true);
  $("#odometer-form [name=odometer]")?.focus?.();
}
async function saveOdometer(payload){
  try{
    if(state.online){ const path=editingOdometerId?`/api/odometer-readings/${editingOdometerId}`:"/api/odometer-readings"; await api(path,{method:editingOdometerId?"PUT":"POST",body:JSON.stringify({...payload,client_id:editingOdometerId ? editingOdometerClientId : payload.client_id,client_revision:editingOdometerId?editingOdometerRevision:1})}); clearOdometerEdit(); toast("里程已保存"); }
    else throw Object.assign(new Error("offline"),{offline:true});
    if(state.trip) await loadDashboard();
  }catch(error){
    if(!error.offline) throw error;
    const clientId=editingOdometerClientId || payload.client_id || newClientId();
    const item={entity:"odometer",op:"upsert",op_target_id:editingOdometerId,client_id:clientId,client_revision:editingOdometerId?editingOdometerRevision:1,payload:{...payload,client_id:clientId,client_revision:editingOdometerId?editingOdometerRevision:1,id:editingOdometerId || undefined},queued_at:new Date().toISOString(),updated_at:new Date().toISOString(),error:null};
    await mutateLocalState(doc=>{ doc.queue.push(item); });
    clearOdometerEdit();
    toast("已保存到手机，恢复网络后同步");
  }
}
async function removeOdometer(row){
  if(!confirm(`确认删除 ${row.odometer} km 的里程记录？`)) return;
  const serverClientId=row.client_id || null, clientId=serverClientId || newClientId();
  const clientRevision=Number(row.client_revision||row.version||1)+1;
  const payload={trip_id:currentTripId(),id:row.id,client_id:clientId,client_revision:clientRevision};
  try{
    if(state.online && row.id){ await api(`/api/odometer-readings/${row.id}`,{method:"DELETE",body:JSON.stringify({...payload,client_id:serverClientId})}); }
    else throw Object.assign(new Error("offline"),{offline:true});
    state.dashboard.odometer_readings=(state.dashboard.odometer_readings||[]).filter(item=>item.id!==row.id); renderOdometers(); await loadTrash(); toast("里程记录已删除");
  }catch(error){
    if(!error.offline){ toast(error.message); return; }
    await mutateLocalState(doc=>{
      doc.queue.push({entity:"odometer",op:"delete",client_id:clientId,client_revision:clientRevision,payload,queued_at:new Date().toISOString(),updated_at:new Date().toISOString(),error:null});
      doc.trash=doc.trash.filter(item=>!(item.entity==="odometer" && ((item.client_id && item.client_id===clientId) || Number(trashRecord(item).id)===Number(row.id))));
      doc.trash.push(localTrashEntry({entity:"odometer",record:row,payload,clientId,tripId:currentTripId()}));
    });
    state.dashboard.odometer_readings=(state.dashboard.odometer_readings||[]).filter(item=>item.id!==row.id); renderOdometers(); toast("已移入本地回收站，恢复网络后同步删除");
  }
}
async function loadTrash(){
  if(!state.trip) return;
  try{
    const body=await api(`/api/trash?trip_id=${state.trip.id}`);
    // v26 后端把消费和独立里程统一放在 records；不再优先 body.entries，避免漏掉里程。
    const records=Array.isArray(body) ? body : (Array.isArray(body.records) ? body.records : []);
    const remote=records.filter(item=>isCurrentTripTrash(item));
    const local=(localState().trash || []).filter(isCurrentTripTrash);
    const keys=new Set(remote.map(trashKey));
    state.trash=remote.concat(local.filter(item=>!keys.has(trashKey(item))));
  }catch(_){
    const all=localState().trash || [], now=Date.now();
    state.trash=all.filter(item=>trashTripId(item)===currentTripId() && !trashExpired(item,now));
    if(state.trash.length!==all.length){
      try{ await mutateLocalState(doc=>{ doc.trash=(doc.trash||[]).filter(item=>!trashExpired(item,now)); }); }catch(__){}
    }
  }
  // 远端回收站成功时也清理手机上已经过期的副本，避免导出时残留旧 tombstone。
  try{
    const local=localState(), now=Date.now();
    if((local.trash||[]).some(item=>trashExpired(item,now))) await mutateLocalState(doc=>{ doc.trash=(doc.trash||[]).filter(item=>!trashExpired(item,now)); });
  }catch(_){}
  renderTrash();
}
function trashRecord(item){ return item.record || item.payload || item; }
function renderTrash(){
  const box=$("#trash-list"); if(!box) return; box.replaceChildren();
  const rows=(state.trash||[]).filter(isCurrentTripTrash);
  if(!rows.length){ const empty=document.createElement("p"); empty.className="empty"; text(empty,"回收站为空"); box.append(empty); return; }
  rows.forEach(item=>{
    const record=trashRecord(item), row=document.createElement("div"); row.className="history-item trash-row";
    const left=document.createElement("div"), title=document.createElement("b"), detail=document.createElement("small"), actions=document.createElement("div");
    text(title,odometerTrash(item) ? `${record.odometer ?? "?"} km` : categoryLabels[record.category] || record.category_label || "记录"); text(detail,[record.location,record.note || record.item,displayEntryTime(item.deleted_at || record.deleted_at)].filter(Boolean).join(" · "));
    actions.className="history-actions"; const restore=document.createElement("button"), remove=document.createElement("button"); restore.type="button"; restore.className="history-button"; text(restore,"恢复"); restore.addEventListener("click",()=>restoreTrash(item));
    remove.type="button"; remove.className="history-button danger"; text(remove,"永久删除"); remove.addEventListener("click",()=>permanentlyDeleteTrash(item));
    actions.append(restore,remove); left.append(title,detail); row.append(left,actions); box.append(row);
  });
}
function sameTrashRecord(left,item){
  const right=trashRecord(item), leftRecord=trashRecord(left), clientId=item?.client_id || right?.client_id;
  const id=right?.id ?? item?.id, sameScope=trashEntity(left)===trashEntity(item) && trashTripId(left)===trashTripId(item);
  return left===item || (sameScope && clientId && (left?.client_id===clientId || leftRecord?.client_id===clientId)) ||
    (sameScope && id!=null && Number(leftRecord?.id ?? left?.id)===Number(id));
}
function queueTripId(item){ return valueTripId(item?.trip_id ?? item?.payload?.trip_id ?? item?.payload?.recognized?.trip_id); }
function sameQueueRecord(left,right){
  return left?.entity===right?.entity && queueTripId(left)===queueTripId(right) &&
    Boolean(left?.client_id && right?.client_id && left.client_id===right.client_id);
}
function queueMatchesTrash(queueItem,item){
  const record=trashRecord(item), clientId=item?.client_id || record?.client_id, id=record?.id ?? item?.id;
  const sameScope=queueItem?.entity===trashEntity(item) && queueTripId(queueItem)===trashTripId(item);
  return Boolean(sameScope && ((clientId && queueItem?.client_id===clientId) ||
    (id!=null && Number(queueItem?.payload?.id ?? queueItem?.op_target_id)===Number(id))));
}
function applyDeleteSyncToTrash(doc,item,result){
  const current=(doc.trash||[]).find(candidate=>queueMatchesTrash(item,candidate));
  const baseRecord=clone(trashRecord(current || {}) || {}), source=clone(item.payload?.recognized || item.payload || {});
  const tripId=queueTripId(item), clientId=item.client_id || baseRecord.client_id || source.client_id || null;
  const serverId=result?.id==null ? (baseRecord.id ?? source.id ?? null) : Number(result.id);
  const deletedAt=result?.deleted_at || current?.deleted_at || baseRecord.deleted_at || new Date().toISOString();
  const expiresAt=result?.delete_expires_at || current?.expires_at || current?.delete_expires_at || baseRecord.delete_expires_at || new Date(Date.now()+30*24*60*60*1000).toISOString();
  const record={...source,...baseRecord,trip_id:tripId,client_id:clientId,client_revision:Number(result?.client_revision || item.client_revision || baseRecord.client_revision || 1),deleted_at:deletedAt,delete_expires_at:expiresAt};
  if(serverId!=null) record.id=serverId; else delete record.id;
  if(item.entity==="odometer") record.record_type="odometer_reading";
  const payload={...clone(current?.payload || item.payload || {}),trip_id:tripId,client_id:clientId,client_revision:record.client_revision};
  if(serverId!=null) payload.id=serverId; else delete payload.id;
  const updated={...clone(current || {}),entity:item.entity,trip_id:tripId,client_id:clientId,record,payload,
    raw_text:current?.raw_text || item.payload?.raw_text || record.raw_text || "",deleted_at:deletedAt,expires_at:expiresAt,
    local_only:serverId==null,server_delete_confirmed:true};
  doc.trash=(doc.trash||[]).filter(candidate=>!queueMatchesTrash(item,candidate));
  doc.trash.push(updated);
  return updated;
}
async function permanentlyDeleteTrash(item){
  if(!isCurrentTripTrash(item)){ renderTrash(); toast("这条回收站记录已过期或不属于当前行程"); return; }
  const initialRecord=trashRecord(item), entity=trashEntity(item);
  const label=odometerTrash(item) ? `${initialRecord.odometer ?? "?"} km 里程记录` : `${categoryLabels[initialRecord.category] || initialRecord.category_label || "消费记录"}${initialRecord.amount!=null ? ` ${money(initialRecord.amount)}` : ""}`;
  if(!confirm(`永久删除“${label}”？\n\n此操作不可恢复，该条记录的修改历史也会一并删除。`)) return;
  if(!state.online){ toast("永久删除必须联网确认，请连接网络后重试"); return; }
  try{
    await withOfflineQueueLock(async()=>{
      let doc=ensureLocalStateLocked();
      const storedItem=(doc.trash||[]).find(current=>sameTrashRecord(current,item)) || item;
      const record=trashRecord(storedItem), clientId=storedItem?.client_id || record?.client_id || null;
      // 远端与手机副本合并展示时，storedItem 可能是没有 id 的旧本地副本。
      // item 已通过当前行程和实体校验，因此可安全回退到界面上的远端 id。
      let serverId=record?.id ?? storedItem?.id ?? initialRecord?.id ?? item?.id ?? null, purgeByClient=false;
      const pendingDelete=(doc.queue||[]).find(current=>current.op==="delete" && queueMatchesTrash(current,storedItem));
      if(pendingDelete){
        const synced=await syncOne(pendingDelete);
        verifySyncResponse(pendingDelete,synced);
        if(synced.tombstone){ serverId=null; purgeByClient=true; }
        else{
          if(synced.id==null) throw new Error("服务器未返回已删除记录编号，手机记录已保留");
          serverId=Number(synced.id);
        }
      }else if(serverId==null){
        if(storedItem?.server_delete_confirmed) purgeByClient=true;
        else throw new Error("找不到这条本机记录的待删除操作，已保留记录；请先同步后重试");
      }
      if(purgeByClient){
        const revision=Math.max(Number(record?.client_revision||0),Number(pendingDelete?.client_revision||0)) || 1;
        let result;
        try{
          result=await api("/api/trash/purge-client",{method:"POST",body:JSON.stringify({entity,trip_id:currentTripId(),client_id:clientId,client_revision:revision})});
        }catch(error){
          if(error.status!==409 || !error.body?.record_id) throw error;
          const retryRevision=Math.max(revision,Number(error.body.client_revision||0))+1;
          const retryPayload={...clone(pendingDelete?.payload || storedItem?.payload || record || {}),trip_id:currentTripId(),id:Number(error.body.record_id),client_id:clientId,client_revision:retryRevision};
          const retry={...clone(pendingDelete || {}),entity,op:"delete",op_target_id:Number(error.body.record_id),client_id:clientId,client_revision:retryRevision,payload:retryPayload,queued_at:pendingDelete?.queued_at || new Date().toISOString(),updated_at:new Date().toISOString(),error:null};
          doc.queue=(doc.queue||[]).filter(current=>!sameQueueRecord(current,retry));
          doc.queue.push(retry);
          const updatedRecord={...clone(record),id:Number(error.body.record_id),trip_id:currentTripId(),client_id:clientId,client_revision:retryRevision};
          const kept={...clone(storedItem),entity,trip_id:currentTripId(),client_id:clientId,record:updatedRecord,payload:retryPayload,local_only:false,server_delete_confirmed:false};
          doc.trash=(doc.trash||[]).filter(current=>!sameTrashRecord(current,storedItem));
          doc.trash.push(kept);
          commitDocUnlocked(doc);
          throw new Error("服务器中记录尚未进入回收站，已重建删除任务；请再试一次");
        }
        if(!result?.permanently_deleted || result.client_id!==clientId || Number(result.trip_id)!==currentTripId()) throw new Error("服务器未确认永久墓碑，手机记录已保留");
      }else if(serverId!=null){
        const path=entity==="entry" ? "entries" : "odometer-readings";
        const revision=Math.max(Number(record?.client_revision||0),Number(pendingDelete?.client_revision||0)) || null;
        const result=await api(`/api/trash/${path}/${serverId}`,{method:"DELETE",body:JSON.stringify({trip_id:currentTripId(),client_id:clientId,client_revision:revision})});
        if(!result?.permanently_deleted || Number(result.record_id)!==Number(serverId)) throw new Error("服务器未确认永久删除，请刷新后重试");
      }
      doc.trash=(doc.trash||[]).filter(current=>!sameTrashRecord(current,storedItem));
      doc.queue=(doc.queue||[]).filter(current=>!queueMatchesTrash(current,storedItem));
      commitDocUnlocked(doc);
    });
    state.trash=(state.trash||[]).filter(current=>!sameTrashRecord(current,item)); renderTrash();
    toast("记录及其修改历史已永久删除");
  }catch(error){ toast(error.offline ? "永久删除必须联网确认，请连接网络后重试" : error.message); }
}
async function restoreTrash(item){
  if(!isCurrentTripTrash(item)){ renderTrash(); toast("这条回收站记录已过期或不属于当前行程"); return; }
  const record=trashRecord(item), id=record.id, entity=odometerTrash(item) ? "odometer" : "entry";
  const existing=offlineQueue().find(current=>queueMatchesTrash(current,item));
  const nextRevision=Math.max(Number(item.client_revision||0),Number(existing?.client_revision||0),Number(record.client_revision||record.version||1))+1;
  if(!id){
    await mutateLocalState(doc=>{
      const index=doc.trash.findIndex(current=>current===item || (item.client_id && current.client_id===item.client_id)); if(index<0) return;
      const stored=doc.trash[index], restored=stored.record || record, clientId=stored.client_id || item.client_id || restored.client_id || newClientId();
      const sourcePayload=stored.payload || item.payload || {};
      const payload=entity==="odometer" ? {...restored,...sourcePayload,trip_id:currentTripId(),client_id:clientId,client_revision:nextRevision} : {...sourcePayload,trip_id:currentTripId(),raw_text:stored.raw_text || sourcePayload.raw_text || restored.raw_text || "",recognized:{...(sourcePayload.recognized||restored),client_id:clientId},client_id:clientId,client_revision:nextRevision};
      doc.queue=doc.queue.filter(current=>!queueMatchesTrash(current,stored));
      doc.queue.push({entity,op:"upsert",client_id:clientId,client_revision:nextRevision,payload,queued_at:new Date().toISOString(),updated_at:new Date().toISOString(),error:null});
      doc.trash.splice(index,1);
    });
    await loadTrash(); renderPending(); toast("本地记录已恢复到待同步队列"); return;
  }
  try{
    const path=entity==="entry" ? "entries" : "odometer-readings";
    if(state.online){
      await withOfflineQueueLock(async()=>{
        let doc=ensureLocalStateLocked();
        const stored=(doc.trash||[]).find(current=>sameTrashRecord(current,item)) || item;
        await api(`/api/${path}/${id}/restore`,{method:"POST",body:JSON.stringify({trip_id:currentTripId(),client_id:item.client_id || record.client_id || null,client_revision:nextRevision})});
        doc.trash=(doc.trash||[]).filter(current=>!sameTrashRecord(current,stored));
        doc.queue=(doc.queue||[]).filter(current=>!queueMatchesTrash(current,stored));
        commitDocUnlocked(doc);
      });
      state.trash=state.trash.filter(current=>!sameTrashRecord(current,item)); renderTrash(); await loadDashboard(); toast("记录已恢复");
    }else throw Object.assign(new Error("offline"),{offline:true});
  }catch(error){
    if(!error.offline){ toast(error.message); return; }
    const clientId=item.client_id || record.client_id || newClientId();
    await mutateLocalState(doc=>{
      doc.queue=doc.queue.filter(current=>!queueMatchesTrash(current,item));
      doc.queue.push({entity,op:"restore",client_id:clientId,client_revision:nextRevision,payload:{trip_id:currentTripId(),id,client_id:clientId,client_revision:nextRevision},queued_at:new Date().toISOString(),updated_at:new Date().toISOString(),error:null});
    });
    toast("恢复操作已保存到手机，恢复网络后同步");
  }
}

function openTripEdit(){
  if(!state.trip) return;
  $("#trip-edit-form [name=name]").value=state.trip.name ?? "";
  $("#trip-edit-form [name=start_odometer]").value=state.trip.start_odometer ?? "";
  $("#trip-edit-form [name=departure_date]").value=state.trip.departure_date || String(state.trip.started_at || "").slice(0,10);
  $("#trip-edit-form [name=planned_days]").value=state.trip.planned_days ?? "";
  show("#trip-edit",true);
  $("#trip-edit-form [name=name]").focus();
}

function closeTripEdit(){
  show("#trip-edit",false);
  const form=$("#trip-edit-form"); if(form) form.reset();
}

function metric(label,value){
  const box=document.createElement("div"); box.className="metric";
  const l=document.createElement("span"); text(l,label);
  const v=document.createElement("strong"); text(v,value);
  box.append(l,v); return box;
}

async function loadDashboard(){
  state.dashboard=await api(`/api/dashboard?trip_id=${state.trip.id}`);
  writeStored(STORAGE.dashboard,state.dashboard);
  renderDashboard();
  try{
    const rows=await api(`/api/odometer-readings?trip_id=${state.trip.id}`);
    state.dashboard.odometer_readings=Array.isArray(rows)?rows:(rows.readings||rows.records||[]);
  }catch(_){ /* 旧后端没有独立里程接口时仍可用本地表单 */ }
  renderOdometers();
  await loadTripDays();
}

function dayRecordsFromDashboard(){
  const rows=state.dashboard?.days;
  return Array.isArray(rows) ? rows : [];
}
async function loadTripDays(){
  if(!state.trip) return;
  const fallback=dayRecordsFromDashboard();
  // 有版本标记时再请求行程日 API，避免旧服务端返回无意义错误。
  if(!state.online || !state.dashboard?.app_version){ state.days=fallback; renderTripDays(); if(state.dashboard) renderV21Overview(state.dashboard); return; }
  try{
    const response=await api(`/api/trips/${state.trip.id}/days`);
    state.days=Array.isArray(response) ? response : (response.days || response.records || fallback);
  }catch(_){
    // A V1.9 server has no days endpoint. Dashboard data remains usable and the
    // screen intentionally stays an empty-state instead of surfacing an error.
    state.days=fallback;
  }
  renderTripDays();
  if(state.dashboard) renderV21Overview(state.dashboard);
}
function dateLabel(value){
  if(!value) return "未设置日期";
  const date=new Date(`${String(value).slice(0,10)}T00:00:00`);
  return Number.isNaN(date.getTime()) ? String(value).slice(0,10) : `${date.getMonth()+1}月${date.getDate()}日`;
}
function routeText(day){
  const start=day.origin || "待定出发地", end=day.destination || "待定目的地";
  return `${start} → ${end}`;
}
function renderTripDays(){
  const box=$("#trip-days"), count=$("#days-count"); if(!box) return;
  const days=(state.days || dayRecordsFromDashboard()).slice().sort((a,b)=>String(a.travel_date||"").localeCompare(String(b.travel_date||"")));
  const planned=Number(state.trip?.planned_days || state.trip?.planned_day_count || 0);
  text(count,planned>0 ? `计划 ${planned} 天` : days.length ? `${days.length} 天` : ""); box.replaceChildren();
  const progress=$("#trip-progress"), progressLabel=$("#trip-progress-label"), progressCount=$("#trip-progress-count"), progressBar=$("#trip-progress-bar");
  if(!days.length){ show("#trip-progress",false); const empty=document.createElement("p"); empty.className="empty"; text(empty,"从第一笔消费或里程开始，沿途每一天会自动整理在这里。"); box.append(empty); return; }
  const todayKey=localDateKey(), todayIndex=days.findIndex(day=>String(day.travel_date).slice(0,10)===todayKey), currentIndex=todayIndex>=0 ? todayIndex : 0, progressTotal=planned>0 ? planned : days.length;
  show("#trip-progress",true); text(progressLabel,todayIndex>=0 ? `Day ${days[currentIndex]?.day_number || currentIndex+1}` : "暂无今天行程"); text(progressCount,`共 ${progressTotal} 天`); if(progressBar) progressBar.style.width=todayIndex>=0 ? `${Math.max(8,Math.min(100,((days[currentIndex]?.day_number || currentIndex+1)/progressTotal)*100))}%` : "0%";
  days.forEach((day,index)=>{
    const isToday=String(day.travel_date).slice(0,10)===todayKey;
    const row=document.createElement("div"); row.className=`day-row${isToday?" current":""}`;
    const header=document.createElement("div"); header.className="day-row__header";
    const badge=document.createElement("span"); badge.className="day-index"; text(badge,`D${day.day_number || index+1}`);
    const copy=document.createElement("div"), title=document.createElement("b"), detail=document.createElement("small");
    text(title,day.title || `${dateLabel(day.travel_date)} · ${routeText(day)}`); if(isToday){ const today=document.createElement("span"); today.className="today-pill"; text(today,"今天"); title.append(today); }
    text(detail,[dateLabel(day.travel_date),routeText(day),day.via ? `途经 ${day.via}` : null,day.note].filter(Boolean).join(" · "));
    copy.append(title,detail); header.append(badge,copy); row.append(header);
    const meta=document.createElement("div"); meta.className="day-row__meta";
    [day.distance_km != null ? `${day.distance_km} km` : null,day.entry_count != null ? `${day.entry_count} 笔记录` : null,day.spend != null ? money(day.spend) : null].filter(Boolean).forEach(value=>{ const span=document.createElement("span"); text(span,value); meta.append(span); });
    if(meta.children.length) row.append(meta);
    const edit=document.createElement("button"); edit.type="button"; edit.className="day-row__edit"; text(edit,"编辑当天路线"); edit.addEventListener("click",()=>editDayRoute(day)); row.append(edit);
    box.append(row);
  });
}
async function editDayRoute(day){
  if(!state.online){ toast("路线编辑需要联网，消费和里程仍可离线记录"); return; }
  const ask=window.prompt; if(typeof ask!=="function"){ toast("当前环境不支持路线编辑"); return; }
  const title=ask("当天标题（可留空）",day.title || ""); if(title===null) return;
  const origin=ask("从哪里出发？",day.origin || ""); if(origin===null) return;
  const destination=ask("到哪里结束？",day.destination || ""); if(destination===null) return;
  const via=ask("途经地（可留空）",day.via || ""); if(via===null) return;
  try{
    await api(`/api/trips/${state.trip.id}/days/${String(day.travel_date).slice(0,10)}`,{method:"PUT",body:JSON.stringify({title:title.trim()||null,origin:origin.trim()||null,destination:destination.trim()||null,via:via.trim()||null})});
    await loadTripDays(); toast("当天路线已更新");
  }catch(error){ toast(error.message); }
}

function renderDashboard(){
  const d=state.dashboard, metrics=$("#metrics"); metrics.replaceChildren();
  metrics.append(
    metric("总支出",money(d.total_spend)), metric("用车成本",money(d.vehicle_cost)),
    metric("行驶里程",d.distance_km==null ? "缺当前里程" : `${d.distance_km} km`),
    metric("百公里油耗",d.fuel.l_per_100km!=null ? `${d.fuel.l_per_100km} L` : "缺连续满箱区间"),
    metric("每公里成本",d.vehicle_cost_per_km!=null ? `¥${Number(d.vehicle_cost_per_km).toFixed(2)}/km` : "缺当前里程")
  );
  renderV21Overview(d);
  const history=$("#history"); history.replaceChildren();
  const summary=$("#category-summary"); summary.replaceChildren();
  Object.entries(d.by_category).forEach(([label,amount])=>{
    const item=document.createElement("span"); item.className="category-total"; text(item,`${label} ${money(amount)}`); summary.append(item);
  });
  renderPending();
  if(!d.entries.length){ const p=document.createElement("p"); p.className="empty"; text(p,"还没有消费记录"); history.append(p); return; }
  d.entries.forEach(entry=>{
    const row=document.createElement("div"); row.className="history-item";
    const left=document.createElement("div"), title=document.createElement("b"), detail=document.createElement("small"), right=document.createElement("div"), amount=document.createElement("strong"), actions=document.createElement("div");
    text(title,categoryLabels[entry.category] || entry.category_label || "消费"); text(detail,[displayEntryTime(entry.occurred_at),entry.location,entry.note || entry.raw_text].filter(Boolean).join(" · ")); text(amount,money(entry.amount));
    actions.className="history-actions"; right.className="history-right";
    const edit=document.createElement("button"), remove=document.createElement("button"), revisions=document.createElement("button");
    edit.type="button"; edit.className="history-button"; text(edit,"修改");
    remove.type="button"; remove.className="history-button danger"; text(remove,"删除");
    revisions.type="button"; revisions.className="history-button"; text(revisions,"修订");
    edit.addEventListener("click",()=>editEntry(entry));
    remove.addEventListener("click",()=>removeEntry(entry));
    revisions.addEventListener("click",()=>toggleRevisions(row,entry));
    actions.append(edit,remove,revisions); right.append(amount,actions); left.append(title,detail); row.append(left,right); history.append(row);
  });
}

function renderV21Overview(d){
  const entries=Array.isArray(d.recent_entries) && d.recent_entries.length ? d.recent_entries : (d.entries || []).slice(0,3);
  const recent=$("#home-recent"); if(recent){
    recent.replaceChildren();
    if(!entries.length){ const empty=document.createElement("p"); empty.className="empty"; text(empty,"第一笔消费会出现在这里。"); recent.append(empty); }
    entries.slice(0,3).forEach(entry=>{
      const row=document.createElement("div"), left=document.createElement("div"), title=document.createElement("b"), detail=document.createElement("small"), amount=document.createElement("strong"); row.className="history-item";
      text(title,categoryLabels[entry.category] || entry.category_label || "消费"); text(detail,[entry.location,entry.note || entry.item,displayEntryTime(entry.occurred_at)].filter(Boolean).join(" · ")); text(amount,money(entry.amount)); left.append(title,detail); row.append(left,amount); recent.append(row);
    });
  }
  const days=(state.days && state.days.length ? state.days : dayRecordsFromDashboard());
  const todayKey=localDateKey(), today=days.find(day=>String(day.travel_date).slice(0,10)===todayKey);
  const title=$("#home-day-title"), route=$("#today-route");
  if(title) text(title,today ? `D${today.day_number || "·"} · ${dateLabel(today.travel_date)}` : "今天");
  if(route){ route.replaceChildren(); const dot=document.createElement("span"), copy=document.createElement("p"); dot.className="route-dot"; text(copy,today ? [routeText(today),today.via ? `途经 ${today.via}` : null,today.spend != null ? `今日 ${money(today.spend)}` : (d.today_spend != null ? `今日 ${money(d.today_spend)}` : null)].filter(Boolean).join(" · ") : "还没有当天路线，记一笔消费或里程后会出现在这里。"); route.append(dot,copy); }
  text($("#stats-total"),money(d.total_spend)); text($("#stats-caption"),d.distance_km!=null ? `已行驶 ${d.distance_km} km · ${d.entries?.length || 0} 笔消费记录` : `已有 ${d.entries?.length || 0} 笔消费记录，补充当前里程后可看到完整用车数据。`);
  const categoriesBox=$("#stats-categories"); if(categoriesBox){
    categoriesBox.replaceChildren(); const pairs=Object.entries(d.by_category || {}), total=Number(d.total_spend||0);
    if(!pairs.length){ const empty=document.createElement("p"); empty.className="empty"; text(empty,"暂无费用数据"); categoriesBox.append(empty); }
    pairs.sort((a,b)=>Number(b[1])-Number(a[1])).forEach(([label,amount])=>{ const row=document.createElement("div"), labelNode=document.createElement("span"), rail=document.createElement("i"), value=document.createElement("b"); row.className="stats-category"; rail.style.setProperty("--percent",total>0 ? `${Math.min(100,Number(amount)/total*100)}%` : "0%"); text(labelNode,label); text(value,money(amount)); row.append(labelNode,rail,value); categoriesBox.append(row); });
  }
  const vehicle=$("#stats-vehicle"); if(vehicle){
    vehicle.replaceChildren(); [
      ["行驶里程",d.distance_km==null ? "待补充" : `${d.distance_km} km`],
      ["用车成本",money(d.vehicle_cost)],
      ["百公里油耗",d.fuel?.l_per_100km!=null ? `${d.fuel.l_per_100km} L` : "待计算"],
      ["每公里成本",d.vehicle_cost_per_km!=null ? `¥${Number(d.vehicle_cost_per_km).toFixed(2)}` : "待计算"]
    ].forEach(([label,value])=>{ const item=document.createElement("div"), l=document.createElement("span"), v=document.createElement("strong"); text(l,label); text(v,value); item.append(l,v); vehicle.append(item); });
  }
}
async function toggleRevisions(row,entry){
  const old=row.querySelector(".revision-list"); if(old){ old.remove(); return; }
  const box=document.createElement("div"); box.className="revision-list"; const loading=document.createElement("div"); loading.className="revision-row"; text(loading,"正在读取修订记录…"); box.append(loading); row.append(box);
  try{
    const result=await api(`/api/entries/${entry.id}/revisions?trip_id=${currentTripId()}`); const rows=Array.isArray(result)?result:(result.revisions||result.records||[]); box.replaceChildren();
    if(!rows.length){ text(loading,"暂无修订记录"); box.append(loading); return; }
    rows.forEach(revision=>{ const item=document.createElement("div"); item.className="revision-row"; text(item,[`v${revision.version}`,revision.action,displayEntryTime(revision.created_at)].filter(Boolean).join(" · ")); box.append(item); });
  }catch(error){ text(loading,error.message); }
}

function renderPending(){
  const container=$("#pending-history"), note=$("#sync-note");
  if(!container || !note) return;
  container.replaceChildren();
  const queue=offlineQueue().filter(item=>!state.trip || Number(item.payload?.trip_id)===Number(state.trip.id));
  show("#sync-note",queue.length>0);
  if(!queue.length){ updateFinishControl(); return; }
  const errors=[...new Set(queue.map(item=>item.error).filter(Boolean))];
  text(note,errors.length
    ? `有 ${queue.length} 笔待同步。失败原因：${errors.join("；")}。记录仍安全保存在手机。`
    : `手机中有 ${queue.length} 笔待同步记录。恢复网络并打开本页后会自动上传；同步前请勿清除浏览器数据。`);
  queue.slice().reverse().forEach(item=>{
    const entry=item.payload?.recognized || item.payload || {}, row=document.createElement("div"); row.className="history-item";
    const left=document.createElement("div"), title=document.createElement("b"), detail=document.createElement("small"), amount=document.createElement("strong"), tag=document.createElement("span");
    text(title,item.entity==="odometer" ? `${entry.odometer ?? "?"} km` : categoryLabels[entry.category] || entry.category || "待确认记录"); text(tag,item.op==="delete"?"待删除":item.op==="restore"?"待恢复":"待同步"); tag.className="pending-tag"; title.append(tag);
    text(detail,[displayEntryTime(entry.occurred_at),entry.location,entry.note || entry.item || item.payload.raw_text].filter(Boolean).join(" · "));
    text(amount,item.entity==="odometer" ? "" : money(entry.amount)); left.append(title,detail); row.append(left,amount);
    const controls=document.createElement("div"); controls.className="pending-edit";
    if(item.op==="upsert" && item.entity==="entry"){
      const edit=document.createElement("button"); edit.type="button"; edit.className="history-button"; text(edit,"修改"); edit.addEventListener("click",()=>editPending(item)); controls.append(edit);
    }
    const remove=document.createElement("button"); remove.type="button"; remove.className="history-button danger"; text(remove,item.op==="delete"?"撤销删除":"删除"); remove.addEventListener("click",()=>removePending(item)); controls.append(remove); row.append(controls);
    if(item.error){ const error=document.createElement("span"); error.className="pending-error"; text(error,`同步失败：${item.error}`); row.append(error); }
    if(item.error?.includes("可能与最近记录重复")){
      const confirmButton=document.createElement("button"); confirmButton.type="button"; confirmButton.className="history-button"; text(confirmButton,"确认重复后同步");
      confirmButton.addEventListener("click",()=>confirmPendingDuplicate(item)); row.append(confirmButton);
    }
    container.append(row);
  });
  updateFinishControl();
}

function openPendingList(){
  if(!state.trip){ toast("开始一段行程后即可查看待处理记录"); return; }
  closeSheets(); setScreen("bills"); renderPending();
  const list=$("#pending-history");
  list?.scrollIntoView?.({behavior:"smooth",block:"start"});
  if(!offlineQueue().length) toast("当前没有待处理记录");
}
function editPending(item){
  if(item.entity!=="entry") return;
  const entry=item.payload?.recognized || item.payload || {};
  renderReview({raw_text:item.payload.raw_text || "",recognized:{...entry,client_id:item.client_id,client_revision:item.client_revision},missing:[],field_meta:item.payload.field_meta || {},derived_fields:[],can_save:false},null,{clientId:item.client_id,clientRevision:item.client_revision,queueItem:item,recordClientIds:[item.client_id]});
  toast("正在修改待同步记录，保存后仍留在手机队列");
}
async function removePending(item){
  if(!confirm(item.op==="delete" ? "撤销这条待删除操作？" : "删除这条待同步记录？")) return;
  await mutateLocalState(doc=>{
    const index=doc.queue.findIndex(current=>current.client_id===item.client_id);
    if(index<0) return;
    const current=doc.queue[index], record=clone(current.payload?.recognized || current.payload || {}), clientId=current.client_id || item.client_id || newClientId();
    const recordId=record.id ?? current.payload?.id ?? null;
    const nextRevision=Number(current.client_revision||1)+1;
    const tripId=valueTripId(current.payload?.trip_id ?? record.trip_id ?? currentTripId());
    if(current.op==="delete"){
      // 撤销删除也必须留下高版本操作；不能直接丢弃，否则服务器可能已经收到删除。
      const restoreOp=recordId==null ? "upsert" : "restore";
      const restorePayload=restoreOp==="upsert"
        ? {...clone(current.payload||{}),trip_id:tripId,client_id:clientId,client_revision:nextRevision,id:recordId ?? undefined}
        : {trip_id:tripId,id:recordId,client_id:clientId,client_revision:nextRevision};
      doc.queue[index]={...current,entity:current.entity||"entry",op:restoreOp,client_id:clientId,client_revision:nextRevision,payload:restorePayload,updated_at:new Date().toISOString(),error:null};
    }else{
      const deletePayload={trip_id:tripId,id:recordId ?? undefined,client_id:clientId,client_revision:nextRevision};
      // 即便原记录尚未拿到服务器ID，也保留 delete tombstone；服务器会以幂等ACK收敛响应丢失场景。
      doc.queue[index]={...current,op:"delete",client_id:clientId,client_revision:nextRevision,payload:deletePayload,updated_at:new Date().toISOString(),error:null};
      const fullPayload=clone(item.payload || current.payload || {});
      const fullRecord=clone(record);
      doc.trash=doc.trash.filter(existing=>!(existing.client_id===clientId || (recordId!=null && Number(trashRecord(existing).id)===Number(recordId))));
      doc.trash.push(localTrashEntry({entity:current.entity,record:fullRecord,payload:fullPayload,clientId,tripId,rawText:fullPayload.raw_text}));
    }
  });
  state.trash=localState().trash; renderPending(); renderTrash(); toast("待同步记录已更新");
}

async function confirmPendingDuplicate(item){
  if(!confirm("这笔离线记录可能与服务器已有记录重复。确认后才会按重复记录保存。")) return;
  try{
    await mutateLocalState(doc=>{ const found=doc.queue.find(current=>current.client_id===item.client_id); if(found) found.payload={...found.payload,confirm_duplicate:true}; });
    await syncOfflineQueue();
    if(state.trip) await loadDashboard(); toast("已按确认的重复记录同步");
  }catch(error){ toast(error.message); }
}

function commitDocUnlocked(doc){
  const current=readV26() || blankLocalState(); doc.schema=LOCAL_SCHEMA; doc.revision=Number(current.revision||0)+1; writeStored(STORAGE.localState,doc);
  const verified=readV26(); if(!verified || verified.revision!==doc.revision) throw new Error("手机本地状态写入校验失败，记录仍保留在当前页面"); state.localState=verified; renderPending(); setOnline(state.online); return verified;
}
async function syncOne(item){
  const payload={entity:item.entity,op:item.op,client_id:item.client_id,client_revision:item.client_revision,payload:item.payload};
  try{ return await api("/api/sync",{method:"POST",body:JSON.stringify(payload)}); }
  catch(error){
    // v25服务端仍可接收普通新建记录；v26删除/恢复必须留在本地等待升级后的服务端。
    if(error.message.includes("请求失败（404") && item.entity==="entry" && item.op==="upsert") return api("/api/entries",{method:"POST",body:JSON.stringify(item.payload)});
    throw error;
  }
}
function verifySyncResponse(item,result){
  if(!result || typeof result!=="object") throw new Error("服务端未返回同步结果，手机记录已保留")
  const responseRevision=result.client_revision ?? result.record?.client_revision;
  if(responseRevision!=null && Number(responseRevision)<Number(item.client_revision||1)){
    throw new Error("服务端修订号未更新，手机记录已保留");
  }
  if(item.op==="delete"){
    if(!["deleted","idempotent"].includes(result.status) && result.deleted!==true){
      throw new Error("服务端未确认删除，手机记录已保留");
    }
    return result;
  }
  const record=result.record || (result.id!=null && (result.category!==undefined || result.odometer!==undefined) ? result : null);
  if(!record || record.id==null) throw new Error("服务端未返回入账记录，手机记录已保留");
  if(record.deleted_at!=null) throw new Error("服务端记录仍在回收站，手机记录已保留");
  if(item.op==="restore") return record;
  const expected=item.payload?.recognized || item.payload || {};
  if(item.entity==="entry"){
    verifyEntryResponse({record},record.id,expected,item.client_revision,item.payload?.raw_text ?? null);
  }else{
    for(const field of ["odometer","occurred_at","location","note"]){
      if(expected[field]===undefined || record[field]===undefined) continue;
      const equal=field==="odometer" ? sameNumber(record[field],expected[field]) : String(record[field] ?? "")===String(expected[field] ?? "");
      if(!equal) throw new Error(`服务端未确认${field}同步，手机记录已保留`);
    }
  }
  return record;
}
async function syncOfflineQueue(){
  if(!state.online || state.syncing) return;
  return withOfflineQueueLock(async()=>{
    let doc=ensureLocalStateLocked(); if(!doc.queue.length) return;
    state.syncing=true;
    state.batch={total:doc.queue.length,done:0,failed:0}; renderSyncProgress();
    try{
    for(const item of doc.queue.slice()){
      try{
        const result=await syncOne(item);
        verifySyncResponse(item,result);
        if(item.op==="delete") applyDeleteSyncToTrash(doc,item,result);
        doc.queue=doc.queue.filter(current=>!sameQueueRecord(current,item));
        if(item.op==="restore") doc.trash=doc.trash.filter(current=>!queueMatchesTrash(item,current));
        doc=commitDocUnlocked(doc);
        state.trash=(doc.trash||[]).filter(isCurrentTripTrash); renderTrash();
        state.batch.done++; renderSyncProgress();
      }catch(error){
        const found=doc.queue.find(current=>sameQueueRecord(current,item)); if(found) found.error=error.message; doc=commitDocUnlocked(doc); state.batch.failed++; state.batch.done++; renderSyncProgress();
        if(error.offline){ setOnline(false); break; }
      }
    }
    if(state.online && state.trip){ await loadDashboard(); toast(offlineQueue().length ? "部分记录同步失败，请稍后重试" : "离线记录已全部同步"); }
    }finally{ state.syncing=false; renderSyncProgress(true); }
  });
}
function renderSyncProgress(done=false){
  const box=$("#sync-progress"), label=$("#sync-progress-label"), bar=$("#sync-progress-bar"); if(!box || !label || !bar) return;
  if(!state.batch || done){ show("#sync-progress",false); return; }
  show("#sync-progress",true); bar.max=state.batch.total || 1; bar.value=state.batch.done; text(label,`正在同步 ${state.batch.done}/${state.batch.total}${state.batch.failed ? `，失败 ${state.batch.failed} 笔` : ""}…`);
}

function fillSelect(){
  const select=$("#entry-form [name=category]");
  const placeholder=document.createElement("option"); placeholder.value=""; text(placeholder,"请选择分类"); select.append(placeholder);
  categories.forEach(([value,label])=>{ const option=document.createElement("option"); option.value=value; text(option,label); select.append(option); });
  select.addEventListener("change",()=>{ updateCategoryFields(select.value); updateReviewState(); });
}

function assign(name,value){
  const control=$(`#entry-form [name=${name}]`); if(!control) return;
  if(value===true) control.value="true"; else if(value===false) control.value="false"; else control.value=value ?? "";
}

function updateCategoryFields(category){
  show("#fuel-fields",category==="fuel");
  show("#item-field",Boolean(category) && category!=="fuel");
  const item=$("#entry-form [name=item]");
  item.placeholder=category && itemExamples[category] ? `例如：${itemExamples[category]}` : "请先选择消费分类";
  document.querySelectorAll(".quick-category").forEach(button=>button.classList.toggle("active",button.dataset.category===category));
}

function updateCalculatedUnitPrice(){
  const amount=num($("#entry-form [name=amount]").value);
  const liters=num($("#entry-form [name=fuel_liters]").value);
  const listed=num($("#entry-form [name=fuel_unit_price]").value);
  const calculated=amount!=null && liters>0 ? amount/liters : null;
  text($("#fuel-unit-price"),listed!=null && calculated!=null ? `挂牌 ¥${listed.toFixed(3)}/升 · 实付折算 ¥${calculated.toFixed(3)}/升` : calculated!=null ? `实付折算单价：¥${calculated.toFixed(3)}/升（未填写挂牌价）` : "实付折算单价：—");
}

function currentGaps(){
  const form=$("#entry-form"), f=new FormData(form), category=f.get("category");
  const gaps=[];
  // 解析器的 missing 还可能含内部字段名；确认页只输出有实际控件可处理的中文提示。
  const special=(state.parsed?.missing || []).flatMap(item=>{
    const mapped={
      transaction_type:{field:"transaction_type",label:"暂不支持的交易类型",reason:"这类交易暂不能自动入账，请改用支持的消费记录"},
      fuel_grade:{field:"fuel_grade",label:"油号",reason:"请按油枪或小票确认油号"},
      multiple_entries:{field:"multiple_entries",label:"包含多笔消费",reason:"请分别确认每笔消费后再入账"}
    }[item.field];
    // 这些是解析安全规则而不是普通字段提示：保留服务端给出的阻塞级别，
    // 仅把用户可见文字换成中文，不能因无控件而放行入账。
    return mapped ? [{...mapped,label:item.label || mapped.label,reason:item.reason || mapped.reason,level:item.level}] : [];
  });
  gaps.push(...special);
  if(!category) gaps.push({field:"category",label:"消费类别",level:"required",reason:"请选择这笔钱属于哪一类"});
  if(num(f.get("amount"))===null) gaps.push({field:"amount",label:"金额",level:"required",reason:"消费金额是入账必填项"});
  if(!f.get("occurred_at")) gaps.push({field:"occurred_at",label:"记录时间",level:"required",reason:"请确认这笔消费发生的时间"});
  if(category==="fuel"){
    if(num(f.get("fuel_liters"))===null) gaps.push({field:"fuel_liters",label:"加油升数",level:"metric",reason:"缺少后无法计算实时百公里油耗"});
    if(num(f.get("odometer"))===null) gaps.push({field:"odometer",label:"当前里程",level:"metric",reason:"缺少后无法计算实时里程和每公里成本"});
  }
  if(category && !f.get("location")) gaps.push({field:"location",label:"消费地点",level:"optional",reason:"用于查看路线上的花费分布"});
  if(category && category!=="fuel" && !f.get("item")) gaps.push({field:"item",label:"消费内容",level:"optional",reason:"用于说明具体买了什么或支付了什么"});
  return gaps;
}

function updateReviewState(){
  if(!state.parsed) return;
  updateCalculatedUnitPrice();
  const gaps=currentGaps(), box=$("#missing-list"); box.replaceChildren();
  if(gaps.length){
    const list=document.createElement("div"); list.className="gaps";
    gaps.forEach(item=>{
      const row=document.createElement("div"); row.className=`gap ${item.level}`;
      const title=document.createElement("b"); text(title,`${item.level==="required" ? "必须补充" : item.level==="metric" ? "影响统计" : "建议补充"}：${item.label}`);
      const reason=document.createElement("span"); text(reason,item.reason); row.append(title,reason); list.append(row);
    }); box.append(list);
  }
  const canSave=!gaps.some(item=>item.level==="required");
  const multiple=state.reviewRecords.length>1;
  text($("#save-state"),canSave ? (multiple ? `可以确认第 ${state.reviewIndex+1}/${state.reviewRecords.length} 笔` : "可以保存") : "暂不能保存");
  text($("#save-entry"),state.editingEntryId!=null ? "保存修改" : multiple ? (state.reviewIndex+1<state.reviewRecords.length ? "确认这笔，继续下一笔" : "确认最后一笔") : "确认入账");
  $("#save-entry").disabled=!canSave;
}

function startManual(category){
  const label=categoryLabels[category];
  renderReview({raw_text:`手动记录：${label}`,recognized:{category,amount:null,location:null,occurred_at:localDateTimeValue(),fuel_grade:category==="fuel" ? 95 : null,full_tank:null},missing:[],derived_fields:[],can_save:false});
}

function setEditingMode(entryId=null){
  state.editingEntryId=entryId;
  text($("#review-title"),entryId==null ? "确认记录" : "修改消费记录");
  text($("#save-entry"),entryId==null ? "确认入账" : "保存修改");
  show("#cancel-edit",entryId!=null);
}

function resetReview(clearEntryText=false){
  cancelNaturalParse(); state.parsed=null; state.reviewRecords=[]; state.reviewRecordClientIds=[]; state.reviewIndex=0; state.gps=null; state.draftClientId=null; state.sourceEdit=null; state.reviewSession++; state.reviewQueueItem=null; state.editingClientId=null; state.editingClientRevision=null; state.editingTargetVersion=null; $("#gps-location").disabled=false; setEditingMode(null); $("#entry-form").reset();
  if(clearEntryText) $("#entry-text").value="";
  $("#record-list").replaceChildren(); show("#record-list",false); show("#record-progress",false); $("#field-meta").replaceChildren(); show("#edit-source-text",false); show("#return-to-review",false); show("#review-card",false); show("#entry-card",Boolean(state.trip)); renderDraftBanner();
}

function renderReview(parsed,entryId=null,context={}){
  state.reviewSource=parsed; state.reviewRecords=Array.isArray(parsed.records) && parsed.records.length ? parsed.records : [parsed]; state.reviewRecordClientIds=Array.isArray(context.recordClientIds) && context.recordClientIds.length===state.reviewRecords.length ? context.recordClientIds.slice() : state.reviewRecords.map(record=>record.client_id || record.recognized?.client_id || newClientId()); state.reviewIndex=Math.min(Math.max(Number(context.recordIndex||0),0),state.reviewRecords.length-1); state.parsed=state.reviewRecords[state.reviewIndex]; state.reviewQueueItem=context.queueItem || (entryId==null ? state.reviewQueueItem : null);
  if(state.reviewQueueItem?.client_id){ state.reviewRecordClientIds[state.reviewIndex]=state.reviewQueueItem.client_id; }
  const recognized=state.parsed?.recognized || parsed.recognized || {};
  state.editingClientId=context.clientId !== undefined ? context.clientId : (entryId!=null ? (recognized.client_id || null) : null);
  state.editingTargetVersion=context.targetVersion !== undefined ? context.targetVersion : (entryId!=null ? (recognized.version || null) : null);
  state.editingClientRevision=context.clientRevision !== undefined ? Number(context.clientRevision) : (entryId!=null ? Number(recognized.client_revision || recognized.version || 1)+1 : null);
  state.draftClientId=state.reviewQueueItem?.client_id || state.reviewRecordClientIds[state.reviewIndex] || parsed.client_id || newClientId();
  state.sourceEdit=null; state.reviewSession++; $("#gps-location").disabled=false; setEditingMode(entryId); show("#entry-card",false); show("#review-card",true); show("#return-to-review",false); show("#edit-source-text",canReidentifySource()); openRecordSheet();
  renderRecordList(); renderActiveReview();
}
function renderRecordList(){
  const box=$("#record-list"), progress=$("#record-progress"); if(!box || !progress) return;
  box.replaceChildren(); const records=state.reviewRecords||[];
  if(records.length<=1){ show("#record-list",false); show("#record-progress",false); return; }
  show("#record-list",true); show("#record-progress",true); text(progress,`本句话识别到 ${records.length} 笔消费，正在确认第 ${state.reviewIndex+1}/${records.length} 笔`);
  records.forEach((record,index)=>{
    const button=document.createElement("button"); button.type="button"; button.className=`record-choice ${index===state.reviewIndex?"active":""}`; button.disabled=index!==state.reviewIndex; const fields=record.recognized||{};
    const label=document.createElement("span"), status=document.createElement("small"); text(label,[categoryLabels[fields.category]||"待分类",fields.amount!=null?money(fields.amount):"金额待补充",fields.location,fields.item||fields.note].filter(Boolean).join(" · ")); text(status,index<state.reviewIndex?"已确认 · 只读":index===state.reviewIndex?"当前":"待确认"); button.append(label,status); box.append(button);
  });
}
function renderActiveReview(){
  const parsed=state.parsed || {}, fields=parsed.recognized || {};
  state.gps=fields.latitude!=null && fields.longitude!=null ? {
    latitude:Number(fields.latitude),longitude:Number(fields.longitude),accuracy:fields.gps_accuracy==null ? null : Number(fields.gps_accuracy),region:fields.region||null
  } : null;
  ["category","amount","location","item","fuel_grade","fuel_liters","fuel_unit_price","odometer","full_tank"].forEach(k=>assign(k,fields[k]));
  assign("occurred_at",fields.occurred_at || localDateTimeValue());
  text($("#gps-status"),"");
  updateCategoryFields(fields.category);
  renderFieldMeta(parsed.field_meta || state.reviewSource?.field_meta || {});
  updateReviewState();
  $("#review-card").scrollIntoView({behavior:"smooth",block:"start"});
}
function renderFieldMeta(meta){
  const box=$("#field-meta"); if(!box) return; box.replaceChildren();
  // 后端元数据可以包含 people/nights/category_label 等内部或派生字段；确认页
  // 只解释当前分类已经呈现的输入项，避免把无关字段误说成“待补充”。
  const labels={category:"分类",amount:"金额",location:"地点",item:"消费内容",occurred_at:"记录时间",fuel_grade:"油号",fuel_liters:"加油升数",fuel_unit_price:"挂牌单价",odometer:"当前里程",full_tank:"是否加满"};
  const category=$("#entry-form [name=category]")?.value || state.parsed?.recognized?.category;
  const visible=new Set(["category","amount","location","occurred_at"]);
  if(category==="fuel") ["fuel_grade","fuel_liters","fuel_unit_price","odometer","full_tank"].forEach(field=>visible.add(field));
  else if(category) visible.add("item");
  Object.entries(meta||{}).forEach(([field,info])=>{
    if(!visible.has(field) || !labels[field]) return;
    const raw=typeof info==="string"?{state:info}:info||{}, stateName=raw.state||"review", control=$(`#entry-form [name=${field}]`);
    const required=["category","amount","occurred_at"].includes(field), metric=["fuel_liters","odometer"].includes(field);
    // “缺失”不是一律红色：地点等可选项中性展示，升数/里程只提示会影响统计；
    // 仅入账必填的分类、金额和时间才需要红色待补。
    let mapped=stateName==="recognized"?"certain":stateName==="derived"?"review":stateName, stateLabel=mapped==="certain"?"已识别":mapped==="review"?"请核对":mapped==="default"?"默认值":"待补充";
    if(stateName==="missing"){
      if(control?.value){ mapped="default"; stateLabel="默认值"; }
      else if(required){ mapped="missing"; stateLabel="待补充"; }
      else if(metric){ mapped="review"; stateLabel="影响统计"; }
      else { mapped="default"; stateLabel="可选"; }
    }
    const reason=raw.reason || raw.evidence || "识别结果，请核对"; const badge=document.createElement("span"); badge.className=`field-badge ${mapped}`; badge.title=reason; text(badge,`${labels[field]}：${stateLabel}`); box.append(badge);
    if(control){ control.classList.remove("field-control-certain","field-control-review","field-control-missing","field-control-default"); control.classList.add(`field-control-${mapped}`); control.title=reason; control.dataset.fieldState=mapped; }
  });
}
function markFieldConfirmed(event){
  const field=event?.target?.name; if(!field || !state.parsed) return;
  state.parsed.field_meta ||= {};
  state.parsed.field_meta[field]={...(state.parsed.field_meta[field]||{}),state:"recognized",reason:"已由用户确认"};
  if(state.sourceEdit) state.sourceEdit.manualChanges=true;
  renderFieldMeta(state.parsed.field_meta);
}
function canReidentifySource(){ return Boolean(state.parsed && !state.savingEntry && state.editingEntryId==null && !state.reviewQueueItem && state.reviewRecords.length===1); }
function editSourceText(){
  if(!canReidentifySource()){
    toast("只有单条未提交的新记录可以重新识别；修改已有记录或多笔记录请直接在确认页调整。"); return;
  }
  captureActiveReview();
  activateSourceEdit({active:true,clientId:state.draftClientId || state.reviewRecordClientIds[0],rawText:$("#entry-text").value || state.parsed.raw_text || "",manualChanges:Object.values(state.parsed.field_meta||{}).some(info=>info?.reason==="已由用户确认")});
  persistDraft().catch(error=>toast(error.message)); $("#entry-text").focus?.();
}
function activateSourceEdit(source,pendingRaw=null){
  state.sourceEdit={active:true,clientId:source.clientId || state.draftClientId || state.reviewRecordClientIds[0],rawText:source.rawText || state.parsed?.raw_text || "",manualChanges:Boolean(source.manualChanges)};
  $("#entry-text").value=pendingRaw==null ? state.sourceEdit.rawText : pendingRaw;
  show("#review-card",false); show("#entry-card",true); show("#return-to-review",true); setEntryMode("natural",true);
}
function returnToReview(){
  const source=state.sourceEdit; if(!source) return;
  if(state.savingEntry){ toast("正在保存，请稍候"); return; }
  // 取消重新识别必须回到原有确认内容，不能让输入区的临时文字改写原句。
  // 同时废弃计时中或已发出的请求，避免晚到响应重新覆盖已回退的确认页。
  cancelNaturalParse();
  $("#entry-text").value=source.rawText;
  state.sourceEdit=null; show("#return-to-review",false); show("#entry-card",false); show("#review-card",true);
  renderActiveReview(); persistDraft().catch(error=>toast(error.message));
}
function advanceReviewRecord(){
  if(state.reviewIndex+1 >= state.reviewRecords.length) return false;
  captureActiveReview();
  state.reviewIndex++; state.parsed=state.reviewRecords[state.reviewIndex]; state.draftClientId=state.reviewRecordClientIds?.[state.reviewIndex] || (state.reviewRecordClientIds[state.reviewIndex]=newClientId()); renderRecordList(); renderActiveReview(); return true;
}

function editEntry(entry){
  renderReview({
    raw_text:entry.raw_text || "",
    recognized:{
      category:entry.category, amount:entry.amount, location:entry.location,
      occurred_at:entry.occurred_at, item:entry.note, fuel_grade:entry.fuel_grade,
      fuel_liters:entry.fuel_liters, fuel_unit_price:entry.fuel_unit_price, odometer:entry.odometer,
      latitude:entry.latitude, longitude:entry.longitude, gps_accuracy:entry.gps_accuracy, region:entry.region,
      client_id:entry.client_id || null, client_revision:Number(entry.client_revision || entry.version || 1), version:entry.version || null,
      full_tank:entry.category==="fuel" ? (entry.full_tank==null ? null : entry.full_tank===1) : null,
      people:entry.people, nights:entry.nights
    }, missing:[], derived_fields:[], can_save:true
  },entry.id);
}

function sameNumber(left,right){
  if(left==null || right==null || left==="" || right==="") return left==null && right==null;
  return Math.abs(Number(left)-Number(right)) < 1e-6;
}
function normalizeLocalOccurredAt(value){
  // 后端保存的是无时区的本地时间，允许表单分钟精度与服务端补齐的 :00
  // 等价；Z、offset 和任何非法字符串都不能被悄悄归一化成同一个时间。
  if(typeof value!=="string") return null;
  const match=value.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/);
  if(!match) return null;
  const [,yearText,monthText,dayText,hourText,minuteText,secondText]=match;
  const year=Number(yearText), month=Number(monthText), day=Number(dayText), hour=Number(hourText), minute=Number(minuteText), second=Number(secondText ?? "0");
  const leap=year%4===0 && (year%100!==0 || year%400===0), days=[31,leap?29:28,31,30,31,30,31,31,30,31,30,31];
  if(year<1 || month<1 || month>12 || day<1 || day>days[month-1] || hour>23 || minute>59 || second>59) return null;
  return `${yearText}-${monthText}-${dayText}T${hourText}:${minuteText}:${String(second).padStart(2,"0")}`;
}
function sameLocalOccurredAt(left,right){
  const normalizedLeft=normalizeLocalOccurredAt(left), normalizedRight=normalizeLocalOccurredAt(right);
  return normalizedLeft!==null && normalizedRight!==null && normalizedLeft===normalizedRight;
}
function verifyEntryResponse(result,entryId,recognized,clientRevision,rawText=null){
  const record=result?.record || result;
  if(!record || Number(record.id)!==Number(entryId)) throw new Error("服务端未确认这次修改，请刷新后重试");
  const checks=[
    ["category",record.category,recognized.category], ["amount",record.amount,recognized.amount],
    ["location",record.location,recognized.location], ["occurred_at",record.occurred_at,recognized.occurred_at],
    ["fuel_grade",record.fuel_grade,recognized.fuel_grade], ["fuel_liters",record.fuel_liters,recognized.fuel_liters],
    ["挂牌单价",record.fuel_unit_price,recognized.fuel_unit_price], ["是否加满",record.full_tank,recognized.full_tank],
    ["odometer",record.odometer,recognized.odometer], ["消费内容",record.note,recognized.item]
  ];
  for(const [field,actual,expected] of checks){
    if(actual===undefined) continue;
    const equal=field==="occurred_at" ? sameLocalOccurredAt(actual,expected) : (typeof expected==="number" || typeof actual==="number" ? sameNumber(actual,expected) : String(actual ?? "")===String(expected ?? ""));
    if(!equal) throw new Error(`服务端未确认${field}修改，请刷新后重试`);
  }
  if(rawText!=null && record.raw_text!==undefined && String(record.raw_text)!==String(rawText)) throw new Error("服务端未确认原文修改，请刷新后重试");
  if(record.client_revision!=null && Number(record.client_revision)<Number(clientRevision)) throw new Error("服务端修订号未更新，请刷新后重试");
  return record;
}

async function removeEntry(entry){
  if(!confirm(`确认删除“${categoryLabels[entry.category] || entry.category_label || "消费"} ${money(entry.amount)}”这笔记录？\n\n记录会进入回收站，30天内可以恢复。`)) return;
  const queueItem={entity:"entry",op:"delete",client_id:entry.client_id || newClientId(),client_revision:Number(entry.client_revision||entry.version||1)+1,payload:{trip_id:state.trip.id,id:entry.id,client_id:entry.client_id || null},queued_at:new Date().toISOString(),updated_at:new Date().toISOString(),error:null};
  try{
    if(!state.online) throw Object.assign(new Error("offline"),{offline:true});
    try{ await api("/api/sync",{method:"POST",body:JSON.stringify(queueItem)}); }
    catch(syncError){
      if(syncError.message.includes("请求失败（404") || !entry.client_id) await api(`/api/entries/${entry.id}`,{method:"DELETE",body:JSON.stringify({trip_id:state.trip.id})});
      else throw syncError;
    }
    if(state.editingEntryId===entry.id) resetReview();
    await loadDashboard(); await loadTrash(); toast("记录已移入回收站，指标已更新");
  }catch(error){
    if(!error.offline){ toast(error.message); return; }
    try{
      const payload={trip_id:state.trip.id,id:entry.id,raw_text:entry.raw_text||"",recognized:clone(entry),client_id:queueItem.client_id,client_revision:queueItem.client_revision};
      await mutateLocalState(doc=>{
        doc.queue.push(queueItem);
        doc.trash=doc.trash.filter(item=>!(item.client_id===queueItem.client_id || Number(trashRecord(item).id)===Number(entry.id)));
        doc.trash.push(localTrashEntry({entity:"entry",record:entry,payload,clientId:queueItem.client_id,tripId:state.trip.id,rawText:entry.raw_text||""}));
      });
      if(state.dashboard) state.dashboard.entries=(state.dashboard.entries||[]).filter(item=>item.id!==entry.id); renderDashboard(); toast("已移入手机回收站，恢复网络后同步删除");
    }catch(storageError){ toast(storageError.message); }
  }
}

document.querySelectorAll(".quick-category").forEach(button=>button.addEventListener("click",()=>startManual(button.dataset.category)));
$("#entry-form").addEventListener("input",event=>{ markFieldConfirmed(event); updateReviewState(); scheduleDraftSave(); });
$("#entry-form").addEventListener("change",event=>{ markFieldConfirmed(event); updateReviewState(); scheduleDraftSave(); });
$("#entry-mode-natural").addEventListener("click",()=>setEntryMode("natural",true));
$("#entry-mode-manual").addEventListener("click",()=>setEntryMode("manual",true));
$("#entry-text").addEventListener("input",()=>{ scheduleDraftSave(); scheduleNaturalParse(); });
$("#cancel-edit").addEventListener("click",()=>resetReview());
$("#edit-source-text")?.addEventListener?.("click",editSourceText);
$("#return-to-review")?.addEventListener?.("click",returnToReview);
$("#edit-trip").addEventListener("click",openTripEdit);
$("#cancel-trip-edit").addEventListener("click",closeTripEdit);
$("#reopen-trip").addEventListener("click",async()=>{
  if(!state.latestFinished) return;
  const button=$("#reopen-trip"); button.disabled=true;
  try{
    state.trip=await api(`/api/trips/${state.latestFinished.id}/reopen`,{method:"POST",body:"{}"});
    state.latestFinished=null; writeStored(STORAGE.trip,state.trip); renderTrip(); await loadDashboard();
    toast("行程已恢复，可以继续记账");
  }catch(error){ toast(error.message); }
  finally{ button.disabled=false; }
});

$("#trip-edit-form").addEventListener("submit",async event=>{
  event.preventDefault(); if(!state.trip) return;
  const form=event.currentTarget, button=form.querySelector("button[type=submit]");
  const formData=new FormData(form), name=String(formData.get("name") || "").trim();
  if(!name){ toast("行程标题必填"); return; }
  button.disabled=true;
  try{
    const updated=await api(`/api/trips/${state.trip.id}`,{
      method:"PUT", body:JSON.stringify({name,start_odometer:num(formData.get("start_odometer")),departure_date:formData.get("departure_date")||null,planned_days:num(formData.get("planned_days"))})
    });
    state.trip=updated; writeStored(STORAGE.trip,state.trip); closeTripEdit(); renderTrip(); await loadDashboard(); toast("行程信息已修改，指标已更新");
  }catch(error){ toast(error.message); }
  finally{ button.disabled=false; }
});

$("#gps-location").addEventListener("click",()=>{
  const button=$("#gps-location"), status=$("#gps-status");
  const session=state.reviewSession, entryId=state.editingEntryId;
  if(!window.isSecureContext){
    text(status,"GPS 定位需要通过 HTTPS 打开本页。");
    toast("当前不是 HTTPS 安全连接，浏览器会禁止 GPS 定位");
    return;
  }
  if(!navigator.geolocation){
    text(status,"当前浏览器不支持 GPS 定位。");
    return;
  }
  button.disabled=true; text(status,"正在获取手机位置……");
  navigator.geolocation.getCurrentPosition(async position=>{
    if(session!==state.reviewSession || entryId!==state.editingEntryId) return;
    const {latitude,longitude,accuracy}=position.coords;
    try{
      const region=await nearestChineseRegion(latitude,longitude);
      if(session!==state.reviewSession || entryId!==state.editingEntryId) return;
      state.gps={latitude,longitude,accuracy,region};
      $("#entry-form [name=location]").value=region;
      markFieldConfirmed({target:{name:"location"}});
      text(status,`${region}；GPS ${latitude.toFixed(6)}, ${longitude.toFixed(6)}；精度约 ${Math.round(accuracy)} 米。`);
    }catch(_){
      if(session!==state.reviewSession || entryId!==state.editingEntryId) return;
      state.gps={latitude,longitude,accuracy,region:null};
      $("#entry-form [name=location]").value=`GPS ${latitude.toFixed(6)}, ${longitude.toFixed(6)}`;
      markFieldConfirmed({target:{name:"location"}});
      text(status,`已保存坐标；离线地区数据尚未缓存，精度约 ${Math.round(accuracy)} 米。`);
    }
    button.disabled=false; updateReviewState(); scheduleDraftSave();
  },error=>{
    if(session!==state.reviewSession || entryId!==state.editingEntryId) return;
    const messages={1:"未获得定位权限，请在浏览器设置中允许位置访问。",2:"暂时无法获取位置，请检查手机定位开关。",3:"定位超时，请到室外后重试。"};
    text(status,messages[error.code] || "GPS 定位失败，请重试。"); button.disabled=false;
  },{enableHighAccuracy:true,timeout:15000,maximumAge:30000});
});

$("#odometer-form").addEventListener("submit",async event=>{
  event.preventDefault(); const form=event.currentTarget, f=new FormData(form), payload={trip_id:currentTripId(),odometer:num(f.get("odometer")),occurred_at:f.get("occurred_at")||localDateTimeValue(),location:f.get("location")||null,note:f.get("note")||null,source:"manual",client_id:newClientId(),client_revision:1};
  const button=form.querySelector("button[type=submit]"); button.disabled=true;
  try{ await saveOdometer(payload); form.reset(); form.querySelector("[name=occurred_at]").value=localDateTimeValue(); setSheetOpen("odometer",false); }
  catch(error){ toast(error.message); }
  finally{ button.disabled=false; }
});

function csvCell(value){ const textValue=value==null?"":String(value); return /[",\n]/.test(textValue) ? `"${textValue.replaceAll('"','""')}"` : textValue; }
function localCsvRows(){
  const headers=["状态","client_id","排队/修改时间","错误","类别","金额","消费时间","地点","内容","油号","升数","里程","纬度","经度","精度","原文"];
  const rows=[headers];
  offlineQueue().filter(item=>currentTripId()==null || valueTripId(item.payload?.trip_id)===currentTripId()).forEach(item=>{
    const p=item.payload?.recognized || item.payload || {}, status=item.error?"同步失败":item.op==="delete"?"待删除":item.op==="restore"?"待恢复":"待同步";
    rows.push([status,item.client_id,item.updated_at||item.queued_at,item.error||"",item.entity==="odometer"?"里程":(categoryLabels[p.category]||p.category||""),p.amount,p.occurred_at,p.location,p.item||p.note,p.fuel_grade,p.fuel_liters,p.odometer,p.latitude,p.longitude,p.gps_accuracy,item.payload?.raw_text||""]);
  });
  (localState().trash||[]).filter(isCurrentTripTrash).forEach(item=>{ const p=item.record||item.payload||{}, odometer=odometerTrash(item); rows.push(["回收站",item.client_id||p.client_id,item.deleted_at||"","",odometer?"里程":(categoryLabels[p.category]||p.category||""),p.amount,p.occurred_at,p.location,p.item||p.note,p.fuel_grade,p.fuel_liters,p.odometer,p.latitude,p.longitude,p.gps_accuracy,item.raw_text||p.raw_text||""]); });
  return rows;
}
function exportLocalBomCsv(){
  const csv="\uFEFF"+localCsvRows().map(row=>row.map(csvCell).join(",")).join("\r\n");
  const blob=new Blob([csv],{type:"text/csv;charset=utf-8"}), url=URL.createObjectURL(blob), anchor=document.createElement("a"); anchor.href=url; anchor.download=`自驾账本-手机待处理-${localDateKey()}.csv`; anchor.click(); setTimeout(()=>URL.revokeObjectURL(url),0); toast("已导出手机待处理记录");
}
$("#export-local").addEventListener("click",exportLocalBomCsv);

$("#trip-form").addEventListener("submit",async event=>{
  event.preventDefault(); const form=event.currentTarget, f=new FormData(form);
  try{
    state.trip=await api("/api/trips",{method:"POST",body:JSON.stringify({name:f.get("name"),origin:f.get("origin"),destination:f.get("destination")||null,departure_date:f.get("departure_date"),planned_days:num(f.get("planned_days")),start_odometer:num(f.get("start_odometer"))})});
    state.latestFinished=null;
    writeStored(STORAGE.trip,state.trip);
    form.reset(); renderTrip(); await loadDashboard(); toast("行程已开始，出发油箱按满油记录");
  }catch(error){ toast(error.message); }
});

$("#parse-form").addEventListener("submit",async event=>{
  event.preventDefault();
  clearTimeout(state.naturalParseTimer);
  await parseNaturalEntry({token:++state.naturalParseToken});
});

$("#entry-form").addEventListener("submit",async event=>{
  event.preventDefault(); if(!state.parsed || !state.trip || state.savingEntry) return;
  const form=event.currentTarget, f=new FormData(form), category=f.get("category");
  const amount=num(f.get("amount")), liters=num(f.get("fuel_liters"));
  const recognized={
    category, amount, location:f.get("location")||null, occurred_at:f.get("occurred_at")||localDateTimeValue(),
    fuel_grade:category==="fuel" ? num(f.get("fuel_grade")) : null,
    fuel_liters:category==="fuel" ? liters : null,
    // 空值代表未提供挂牌价；显示的实付折算单价只是提示，绝不伪装成挂牌价入库。
    fuel_unit_price:category==="fuel" ? num(f.get("fuel_unit_price")) : null,
    odometer:category==="fuel" ? num(f.get("odometer")) : null,
    full_tank:category==="fuel" ? (f.get("full_tank")==="" ? null : f.get("full_tank")==="true") : null,
    people:["meal","ticket"].includes(category) ? state.parsed.recognized.people||null : null,
    nights:category==="lodging" ? state.parsed.recognized.nights||null : null,
    item:category!=="fuel" ? f.get("item")||null : null,
    note:category!=="fuel" ? f.get("item")||null : null,
    latitude:state.gps?.latitude ?? null, longitude:state.gps?.longitude ?? null,
    gps_accuracy:state.gps?.accuracy ?? null, region:state.gps?.region ?? null
  };
  const entryId=state.editingEntryId;
  const clientId=state.reviewQueueItem?.client_id || (entryId==null ? (state.draftClientId || newClientId()) : (state.editingClientId || state.parsed.recognized?.client_id || null));
  const payload={trip_id:state.trip.id,raw_text:$("#entry-text").value || state.parsed.raw_text || "",recognized,
    client_id:clientId,client_revision:entryId!=null ? Number(state.editingClientRevision || state.parsed.recognized?.client_revision || state.parsed.recognized?.version || 1) : Number(state.reviewQueueItem?.client_revision || state.parsed.recognized?.client_revision || 1),id:entryId || undefined};
  const path=entryId==null ? "/api/entries" : `/api/entries/${entryId}`;
  const method=entryId==null ? "POST" : "PUT";
  const submitButton=$("#save-entry"); state.savingEntry=true; submitButton.disabled=true;
  try{
    if(state.reviewQueueItem){
      await mutateLocalState(doc=>{
        const found=doc.queue.find(item=>item.client_id===state.reviewQueueItem.client_id);
        if(!found) throw new Error("待同步记录已被其他标签修改，请刷新后重试");
        // 待同步编辑仍是同一条本地记录，固定 client_id；内容变更必须升一个
        // 客户端修订号，以覆盖“服务器已收到旧版本但响应丢失”的场景。
        const nextRevision=Number(found.client_revision||1)+1;
        Object.assign(found,{entity:"entry",op:"upsert",payload:{...payload,client_id:found.client_id,client_revision:nextRevision},client_id:found.client_id,client_revision:nextRevision,updated_at:new Date().toISOString(),error:null});
        doc.draft=null;
      });
      state.reviewQueueItem=null; resetReview(true); toast("待同步记录已修改"); return;
    }
    let serverResult;
    try{
      serverResult=await api(path,{method,body:JSON.stringify(payload)});
    }catch(error){
      if(!error.message.includes("可能与最近记录重复") || !confirm(`${error.message}\n\n仍然保存吗？`)) throw error;
      serverResult=await api(path,{method,body:JSON.stringify({...payload,confirm_duplicate:true})});
    }
    if(entryId!=null) verifyEntryResponse(serverResult,entryId,recognized,payload.client_revision,state.parsed.raw_text);
    await mutateLocalState(doc=>{ if(doc.draft && Number(doc.draft.trip_id)===currentTripId() && doc.draft.client_id===clientId) doc.draft=null; });
    if(advanceReviewRecord()){
      await persistDraft(); toast(`第 ${state.reviewIndex}/${state.reviewRecords.length} 笔已入账，请确认下一笔`); return;
    }
    resetReview(entryId==null); await loadDashboard(); toast(entryId==null ? "已经入账" : "修改已保存，指标已更新");
  }catch(error){
    if((error.offline || error.uncertain) && entryId==null){
      try{
        // 超时同样进入稳定 client_id 的待处理队列：服务端即使已收到，重试也会按
        // 幂等键核对，而不是产生第二笔账。
        await mutateLocalState(doc=>{ doc.queue.push({entity:"entry",op:"upsert",client_id:clientId,client_revision:Number(payload.client_revision||1),payload,queued_at:new Date().toISOString(),updated_at:new Date().toISOString(),error:error.uncertain ? "请求超时，结果待核对" : null}); doc.draft=null; });
        if(advanceReviewRecord()){ await persistDraft(); toast(error.uncertain ? `第 ${state.reviewIndex}/${state.reviewRecords.length} 笔结果待核对，已加入待处理` : `第 ${state.reviewIndex}/${state.reviewRecords.length} 笔已保存到手机`); }
        else { resetReview(true); toast(error.uncertain ? "请求超时，已标记结果待核对；恢复连接后将按同一记录核验" : "已安全保存到手机，恢复网络后自动同步"); }
      }catch(storageError){ toast(storageError.message); }
    }else if((error.offline || error.uncertain) && entryId!=null){
      try{
        await mutateLocalState(doc=>{ doc.queue.push({entity:"entry",op:"upsert",client_id:clientId,client_revision:Number(payload.client_revision||1),payload,queued_at:new Date().toISOString(),updated_at:new Date().toISOString(),error:error.uncertain ? "请求超时，结果待核对" : null}); doc.draft=null; });
        resetReview(true); toast(error.uncertain ? "修改请求超时，已标记结果待核对" : "修改已保存到手机，恢复网络后同步");
      }catch(storageError){ toast(storageError.message); }
    }else toast(error.offline ? "离线时暂不支持修改已有记录" : error.message);
  }finally{
    state.savingEntry=false;
    if(state.parsed) updateReviewState(); else submitButton.disabled=false;
  }
});

$("#finish-form").addEventListener("submit",async event=>{
  event.preventDefault(); const form=event.currentTarget, f=new FormData(form);
  const endOdometer=num(f.get("end_odometer")), requirements=finishRequirements();
  if(!requirements.valid){
    toast(requirements.minimum===null ? "请填写有效结束里程" : `请填写不小于 ${requirements.minimum} km 的有效结束里程`); updateFinishControl(); return;
  }
  if(!requirements.confirmed){ toast("请先勾选结束确认"); updateFinishControl(); return; }
  if(requirements.pending){ toast(`还有 ${requirements.pending} 笔待同步，请同步完成后再结束行程`); return; }
  const tripName=state.trip.name, total=state.dashboard ? money(state.dashboard.total_spend) : "待统计";
  if(!confirm(`最后确认结束“${tripName}”？\n\n结束里程：${endOdometer} km\n当前总支出：${total}\n\n账单不会删除，误操作后可以恢复。`)) return;
  try{
    const finished=await api(`/api/trips/${state.trip.id}/finish`,{method:"POST",body:JSON.stringify({end_odometer:endOdometer})});
    state.latestFinished=finished; state.trip=null; state.dashboard=null; writeStored(STORAGE.trip,null); writeStored(STORAGE.dashboard,null);
    form.reset(); $("#finish-card").open=false; renderTrip(); toast("行程已结束；如为误操作，可立即恢复");
  }catch(error){ toast(error.message); }
});

document.querySelectorAll("[data-go-screen]").forEach(button=>button.addEventListener("click",()=>{ closeSheets(); setScreen(button.dataset.goScreen); }));
$("#open-record-sheet").addEventListener("click",openRecordSheet);
$("#open-odometer-sheet").addEventListener("click",openOdometerSheet);
document.querySelectorAll("[data-open-odometer]").forEach(button=>button.addEventListener("click",openOdometerSheet));
$("#open-pending")?.addEventListener?.("click",openPendingList);
$("#sheet-backdrop").addEventListener("click",closeSheets);
document.querySelectorAll("[data-close-sheet]").forEach(button=>button.addEventListener("click",closeSheets));
document.addEventListener?.("keydown",event=>{
  if(event.key==="Tab"){ trapSheetFocus(event); return; }
  if(event.key!=="Escape") return;
  const recordOpen=$("#record-sheet")?.classList?.contains?.("open"), odometerOpen=$("#odometer-sheet")?.classList?.contains?.("open");
  if(recordOpen || odometerOpen){ event.preventDefault?.(); closeSheets(); }
});
$("#finish-form")?.addEventListener?.("input",updateFinishControl);
$("#finish-form")?.addEventListener?.("change",updateFinishControl);
// V3.1 keeps a three-state full-tank fact (true / false / unknown); old drafts
// are not silently promoted to a full tank during the local-schema migration.
function ensureFullTankControl(){
  if($("#entry-form [name=full_tank]")) return;
  const odometer=$("#entry-form [name=odometer]"); if(!odometer) return;
  const label=document.createElement("label"); text(label,"是否加满");
  const select=document.createElement("select"); select.name="full_tank";
  [["","未确认"],["true","已加满"],["false","未加满"]].forEach(([value,labelText])=>{ const option=document.createElement("option"); option.value=value; text(option,labelText); select.append(option); });
  select.value=""; label.append(select); odometer.closest("label")?.after(label);
  const note=$("#fuel-fields .field-note"); if(note) text(note,"实付、油价、升数、计算金额和优惠差额会分别保存。");
}
function syncVisualViewport(){
  const viewport=window.visualViewport, fallbackHeight=Number(window.innerHeight);
  const rawHeight=Number(viewport?.height), rawOffset=Number(viewport?.offsetTop);
  const hasViewportHeight=Number.isFinite(rawHeight) && rawHeight>0, hasLayoutHeight=Number.isFinite(fallbackHeight) && fallbackHeight>0;
  // 浏览器 resize 期间 visualViewport 可能晚一帧才更新；使用两者较小值
  // 既保留软键盘缩小的可视高度，也不会让过期的大值撑出短屏弹层。
  const height=hasViewportHeight && hasLayoutHeight ? Math.min(rawHeight,fallbackHeight) : (hasViewportHeight ? rawHeight : (hasLayoutHeight ? fallbackHeight : 0));
  const maxOffset=hasLayoutHeight ? Math.max(0,fallbackHeight-height) : Infinity;
  const offsetTop=viewport && Number.isFinite(rawOffset) && rawOffset>=0 ? Math.min(rawOffset,maxOffset) : 0;
  const visibleBottom=offsetTop+height;
  const inset=viewport && Number.isFinite(fallbackHeight) ? Math.max(0,fallbackHeight-visibleBottom) : 0;
  const root=document.documentElement?.style;
  root?.setProperty("--visual-height",`${height}px`);
  root?.setProperty("--visual-bottom-inset",`${inset}px`);
}
ensureFullTankControl(); syncVisualViewport(); window.visualViewport?.addEventListener("resize",syncVisualViewport); window.visualViewport?.addEventListener("scroll",syncVisualViewport); window.addEventListener?.("resize",syncVisualViewport);
setScreen("home");

fillSelect(); bootstrap();
if("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(()=>{});
window.addEventListener?.("online",async()=>{ try{ await api("/api/health"); setOnline(true); await syncOfflineQueue(); }catch(_){ setOnline(false); } });
window.addEventListener?.("offline",()=>setOnline(false));
document.addEventListener?.("visibilitychange",()=>{ if(!document.hidden && offlineQueue().length) bootstrap(); });

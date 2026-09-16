// Use a disposable local fixture server. Does not inspect a user's browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.TEST_BASE_URL;
assert.ok(base && /^http:\/\/127\.0\.0\.1:\d+$/.test(base), 'Explicit isolated loopback TEST_BASE_URL required');
const swSource = fs.readFileSync(path.join(__dirname, '../static/sw.js'), 'utf8');
const cacheName = swSource.match(/const CACHE="([^"]+)"/)[1];
const assets = JSON.parse(swSource.match(/const ASSETS=(\[[^;]+\]);/)[1]);
(async () => {
  const browser = await chromium.launch({headless:true, ...(process.env.CHROMIUM_EXECUTABLE ? {executablePath:process.env.CHROMIUM_EXECUTABLE} : {})});
  try {
    const context = await browser.newContext({serviceWorkers:'allow',viewport:{width:711,height:584}});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e=>errors.push(e.message));
    await page.goto(base);
    await page.waitForFunction(()=>navigator.serviceWorker.controller !== null);
    const cached = await page.evaluate(async ({name,assets})=>{
      const cache = await caches.open(name);
      return Promise.all(assets.map(async url=>{
        const response=await cache.match(url);
        return {url,status:response?.status,length:response ? (await response.text()).length : 0};
      }));
    }, {name:cacheName,assets});
    for (const item of cached) {
      assert.equal(item.status,200, `Missing cache resource ${item.url}`);
      assert.ok(item.length>0, `Empty cache resource ${item.url}`);
    }
    await context.setOffline(true);
    await page.reload({waitUntil:'domcontentloaded'});
    await page.waitForFunction(()=>document.querySelector('#connection')?.textContent.includes('离线'));
    const offline = await page.evaluate(()=>({
      css:[...document.styleSheets].filter(s=>s.href?.includes('styles.css')).reduce((n,s)=>n+s.cssRules.length,0),
      grid:getComputedStyle(document.querySelector('.app-screen.active')).display,
      rail:document.querySelector('.bottom-nav').getBoundingClientRect().width,
      script:[...document.scripts].map(s=>s.src).find(s=>s.includes('app.js')),
    }));
    assert.ok(offline.css>10,'Offline CSS must be available');
    assert.equal(offline.grid,'grid','Offline app retains layout');
    assert.ok(offline.rail>=87 && offline.rail<=89,'711px offline layout retains 88px rail');
    assert.ok(offline.script.endsWith(assets.find(a=>a.startsWith('/app.js?'))));
    assert.deepEqual(errors,[]);
    console.log(`PASS: ${cacheName} ${assets.length} cached assets, offline HTML/CSS/JS and 711px layout. No production access.`);
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});

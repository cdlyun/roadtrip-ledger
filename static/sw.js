const CACHE="roadtrip-ledger-v3.2.3";
const ASSETS=["/","/styles.css?v=3.2.3","/app.js?v=3.2.3","/manifest.webmanifest","/china_regions.json","/assets/icon-home.svg","/assets/icon-route.svg","/assets/icon-receipt.svg","/assets/icon-chart.svg","/assets/icon-plus.svg"];
self.addEventListener("install",event=>event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(ASSETS)).then(()=>self.skipWaiting())));
self.addEventListener("activate",event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener("fetch",event=>{
  if(event.request.method!=="GET"||new URL(event.request.url).pathname.startsWith("/api/")) return;
  if(event.request.mode==="navigate"){
    event.respondWith(fetch(event.request).then(response=>{
      const copy=response.clone(); caches.open(CACHE).then(cache=>cache.put("/",copy)); return response;
    }).catch(()=>caches.match("/")));
  }else event.respondWith(caches.match(event.request).then(cached=>cached||fetch(event.request)));
});

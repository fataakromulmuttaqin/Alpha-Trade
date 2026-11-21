/* Alpha-Trade SW: network-first (update instan), cache sbg fallback offline.
   API selalu network. Ganti CACHE versi bila struktur shell berubah. */
const CACHE = "at-shell-v2";
const SHELL = ["./", "./index.html", "./style.css", "./app.js", "./config.js",
               "./theme-glass.css", "./manifest.json", "./icon.svg",
               "./vendor/lightweight-charts.standalone.production.js"];
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
  ).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/api/") || e.request.method !== "GET") return;
  e.respondWith(
    fetch(e.request).then((resp) => {
      if (resp.ok && url.origin === location.origin) {
        const copy = resp.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
      }
      return resp;
    }).catch(() => caches.match(e.request).then((hit) => hit || Response.error()))
  );
});

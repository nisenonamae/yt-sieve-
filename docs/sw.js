// インストール可能にするための最小限のService Worker。
// データは常に最新を取りたいので、キャッシュするのは外枠だけ。
const SHELL = "furui-shell-v1";
const FILES = ["./", "./index.html", "./manifest.webmanifest"];

self.addEventListener("install", e => {
  self.skipWaiting();
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(FILES)).catch(() => {}));
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys().then(ks => Promise.all(ks.filter(k => k !== SHELL).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;

  // data/*.json と共有からの遷移は必ずネットワークを見る
  if (url.pathname.includes("/data/") || url.search.includes("text=") || url.search.includes("url=")) return;

  e.respondWith(
    fetch(e.request)
      .then(r => {
        const copy = r.clone();
        caches.open(SHELL).then(c => c.put(e.request, copy)).catch(() => {});
        return r;
      })
      .catch(() => caches.match(e.request).then(r => r || caches.match("./index.html")))
  );
});

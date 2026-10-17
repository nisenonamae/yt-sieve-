// 関所のサービスワーカー:つながるときは必ず最新を取りに行き、つながらないときだけ手元の保存を使う
const CACHE = "sekisho-v23";
self.addEventListener("install", e => { self.skipWaiting(); });
self.addEventListener("activate", e => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k !== CACHE) await caches.delete(k);   // 古い保存(前のふるいの分も)を消す
    await self.clients.claim();
  })());
});
self.addEventListener("fetch", e => {
  const req = e.request; if (req.method !== "GET") return;
  const url = new URL(req.url); if (url.origin !== location.origin) return;       // GitHubやYouTubeへの問い合わせには手を出さない
  e.respondWith((async () => {
    try { const res = await fetch(req, { cache: "no-store" }); const c = await caches.open(CACHE); c.put(req, res.clone()); return res; }
    catch (err) { const hit = await caches.match(req); if (hit) return hit; throw err; }
  })());
});

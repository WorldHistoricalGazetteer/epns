/* sw.js: keeps the page's own code and small files so the explorer starts with no network.
 *
 * What it does NOT keep: the big data files (core.json, the county files, the name index and the
 * phonetic matrix). store.js already holds those in IndexedDB under the sha256 digests of the
 * manifest, and a second copy here would double 200 MB of storage for nothing. Basemap tiles and
 * every other cross-origin request are left alone and need a network.
 *
 * Policy:
 *   vendor/**           cache first. Pinned third-party code (docs/vendor/README.md); never changes
 *                       under the same path.
 *   everything else     network first, but give up on the network after 4 s and serve the saved copy,
 *   (this origin)       so a dead or captive wifi costs four seconds, not a hang. The saved copy
 *                       is matched ignoring ?v=…, so a version bump on the page never strands it.
 *   any-ascii on the    symphonym/preprocess.js imports it from cdn.jsdelivr.net and must not be
 *   jsdelivr CDN        edited (it is hash-pinned to whg3's), so the request is answered from the
 *                       vendored copy instead: the same code at the same version, no CDN needed.
 *
 * Bump CACHE when this policy changes; old caches are deleted on activation. Page code is not
 * versioned here: it is refreshed from the network whenever there is one.
 */
const CACHE = 'deep-shell-1';
const SCOPE = self.registration.scope;
const u = (p) => new URL(p, SCOPE).href;

/* Paths only: the lookup ignores the query string, so none of these needs a version. */
const SHELL = [
  './', 'index.html', 'downloads.html', 'favicon.svg', 'css/app.css',
  'js/app.js', 'js/config.js', 'js/store.js', 'js/formats.js', 'js/rdf.js', 'js/search.worker.js',
  'symphonym/preprocess.js', 'symphonym/unicode.js',
  'vendor/maplibre-gl/maplibre-gl.js', 'vendor/maplibre-gl/maplibre-gl.css', 'vendor/any-ascii/any-ascii.mjs',
  'data/manifest.json', 'data/plato-sample.json', 'data/rdf/context.jsonld', 'data/rdf/rules.json',
  'data/symphonym/manifest.json',
];
const BIG = ['/data/core.json', '/data/county/', '/data/names/', '/data/symphonym/emb-', '/symphonym/emb-'];
const ANY_ASCII = /^\/npm\/any-ascii@0\.3\.3\//;

self.addEventListener('install', (e) => {
  e.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    // one by one: a single missing file must not stop the rest from being kept
    await Promise.all(SHELL.map((p) => cache.add(u(p)).catch((err) => console.warn('[sw] not precached', p, err))));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k.startsWith('deep-shell-') && k !== CACHE) await caches.delete(k);
    await self.clients.claim();
  })());
});

async function cacheFirst(req) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(req, { ignoreSearch: true });
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) cache.put(req, res.clone());
  return res;
}

async function networkFirst(req, ms) {
  const cache = await caches.open(CACHE);
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), ms);
  try {
    const res = await fetch(req, { signal: ctl.signal });
    clearTimeout(timer);
    if (res.ok) cache.put(req, res.clone());
    return res;
  } catch (err) {
    clearTimeout(timer);
    const hit = await cache.match(req, { ignoreSearch: true }) || (req.mode === 'navigate' ? await cache.match(u('index.html')) : null);
    if (hit) return hit;
    throw err;
  }
}

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.hostname === 'cdn.jsdelivr.net' && ANY_ASCII.test(url.pathname)) {
    e.respondWith(cacheFirst(u('vendor/any-ascii/any-ascii.mjs')));
    return;
  }
  if (url.origin !== location.origin || !url.href.startsWith(SCOPE)) return;     // tiles, CDNs, other sites
  const path = url.pathname.slice(new URL(SCOPE).pathname.length - 1);
  if (BIG.some((b) => path.startsWith(b))) return;                                // IndexedDB holds these
  if (req.headers.has('range')) return;
  if (path.startsWith('/vendor/')) e.respondWith(cacheFirst(req));
  else e.respondWith(networkFirst(req, 4000));
});

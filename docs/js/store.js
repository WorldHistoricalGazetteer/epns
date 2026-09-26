/* store.js — the IndexedDB cache for fetched data files.
 *
 * Everything the page downloads beyond the HTML itself goes through `cached()`: the boot index,
 * the name index, each county file as it is opened, and the phonetic matrix. Each is stored under
 * its path with the sha256 the manifest published for it, and is served from the cache only while
 * the manifest still carries that digest. The manifest itself is always fetched `no-cache`, so a
 * rebuild that changes one county file invalidates that one file and nothing else, and a returning
 * visitor pays for the 7 MB name index once rather than on every visit.
 *
 * IndexedDB can be absent or refuse (private windows, storage pressure, a locked-down browser).
 * Every operation here is therefore best-effort: a failed read is a miss, a failed write is
 * ignored, and the page works, only without the cache. The pattern is the one in the sibling
 * repositories' stores (premodern-rivers docs/js/store.js, markets docs/js/rr/store.js).
 */
const DB_NAME = 'deep-epns';
const DB_VERSION = 1;
const STORE = 'files';

let dbPromise = null;

function open() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve) => {
    try {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE, { keyPath: 'key' });
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => resolve(null);
      req.onblocked = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
  return dbPromise;
}

async function get(key) {
  const db = await open();
  if (!db) return null;
  return new Promise((resolve) => {
    try {
      const tx = db.transaction(STORE, 'readonly');
      const req = tx.objectStore(STORE).get(key);
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

async function put(key, version, data) {
  const db = await open();
  if (!db) return false;
  return new Promise((resolve) => {
    try {
      const tx = db.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).put({ key, version, data, stored: Date.now() });
      tx.oncomplete = () => resolve(true);
      tx.onerror = () => resolve(false);
      tx.onabort = () => resolve(false);
    } catch {
      resolve(false);
    }
  });
}

/* Fetch `path` (relative to docs/data/) as JSON or ArrayBuffer, through the cache.
 * `version` is the digest the manifest gives for the file; `onProgress(loadedBytes, totalBytes)`
 * reports download progress for the large files. Returns the parsed data. */
export async function cached(path, version, { binary = false, onProgress = null, base = null } = {}) {
  const key = path;
  const hit = await get(key);
  if (hit && hit.version === version) return hit.data;
  /* `base` is an absolute URL for docs/data/. A worker's default base would be its own js/
     directory, so the main thread passes the resolved one in; the page can leave it null. */
  const root = base || new URL('data/', typeof document !== 'undefined' ? document.baseURI : self.location.href).href;
  const url = new URL(path, root);
  url.searchParams.set('v', String(version).slice(0, 16));
  const resp = await fetch(url.href);
  if (!resp.ok) throw new Error(`${path}: HTTP ${resp.status}`);
  let buf;
  const total = Number(resp.headers.get('content-length')) || 0;
  if (onProgress && resp.body) {
    const reader = resp.body.getReader();
    const chunks = [];
    let loaded = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      loaded += value.byteLength;
      onProgress(loaded, total);
    }
    buf = new Uint8Array(loaded);
    let o = 0;
    for (const c of chunks) { buf.set(c, o); o += c.byteLength; }
    buf = buf.buffer;
  } else {
    buf = await resp.arrayBuffer();
  }
  const data = binary ? buf : JSON.parse(new TextDecoder().decode(buf));
  put(key, version, data);   // not awaited: the caller has its data whether or not the cache takes it
  return data;
}

/* Drop everything: the modal offers this so a visitor can reclaim the space. */
export async function clearAll() {
  const db = await open();
  if (!db) return;
  await new Promise((resolve) => {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).clear();
    tx.oncomplete = resolve;
    tx.onerror = resolve;
  });
}

/* Rough size of what is cached, for the modal. */
export async function usage() {
  try {
    const est = await navigator.storage?.estimate?.();
    return est?.usage || 0;
  } catch {
    return 0;
  }
}

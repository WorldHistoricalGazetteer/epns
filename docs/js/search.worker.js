/* search.worker.js — the name index and both kinds of search, off the main thread.
 *
 * TEXT SEARCH scans the distinct name keys (names/keys.json, ~half a million strings) with an
 * ASCII-folded substring match, ranks exact > prefix > word-start > substring, and expands the
 * winning keys to their rows (names/NN.json: every headword, variant spelling and normalised search
 * term, sorted by key so a key's rows are one contiguous range found by binary search).
 *
 * PHONETIC SEARCH embeds the query with Symphonym v8 in the browser (onnxruntime-web, WASM,
 * single-threaded so GitHub Pages needs no cross-origin isolation) and ranks the precomputed int8
 * matrix (data/symphonym/emb-NN.i8, row-aligned with the first `nEmbedded` keys) by dot product.
 * The model, its vocabularies and the tokeniser are whg3's (docs/symphonym/), and every asset is
 * re-hashed on arrival: a v8 model paired with v7 vocabularies raises nothing and embeds plausible
 * nonsense, and an English smoke test passes it (whg3 place#283). Nothing is fetched until the
 * visitor turns the phonetic toggle on; after that it is cached in IndexedDB.
 *
 * `nameKey` below MUST match process/export_site.py `name_key`: the corpus was keyed with the
 * Python, the query is keyed here, and a divergence makes a spelling unreachable without any error.
 */
import { cached } from './store.js';

const DIM = 128;
/* Ranking is by SOUND, gated by SPELLING, the shape whg3's Map Your Data uses (it ranks phonetically
   and applies Sørensen–Dice over character bigrams, lifted to 1 when one name's words are wholly
   contained in the other's, only as a VETO on a bad match; it never blends the two). Symphonym's
   cosine alone is too compressed at the top to rank with (thousands of keys clear 0.70 for any
   query, and "hullampton" sat at 0.89 for "york"); a blend lets a high cosine drag junk up; a veto
   removes it and leaves genuine variants untouched, because Dice on real spelling variants is high
   anyway. Measured on 34 queries with reachable targets (26 Sep 2026, 'en' tags, derived keys):
   place-level top-3 32/34 for cosine, blend and veto alike; top-1 30/34 for the veto at 0.35, with
   the specific junk hits gone and the Cambridge list unchanged. */
const PHON_TOPK = 300;       // phonetic candidates considered
const DICE_VETO = 0.35;      // a candidate spelt less like the query than this is withheld
const COS_MIN = 0.70;        // and the cosine floor WHG's index uses still applies to the survivors
const ORT_DIST = 'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.27.0/dist/';
const ASSETS = {               // sha256 of the v8 set shipped in docs/symphonym/ (md5s in the provenance file)
  'symphonym.onnx':    { sha256: 'b674453fc9d5cc4e5f1a723dba246e0840982d2ab40c575760e3246cee43ddd3', bytes: 8472929 },
  'char_vocab.json':   { sha256: 'a5a2da8c9161b0d455d437fcedd00e45d96cee72947a8a9d0bbbe1dac1ad9ab9', bytes: 2137210 },
  'lang_vocab.json':   { sha256: 'cf92fe8a476a4788f0bf3a597ada04a67f593928a84858852817bc05683f6c4b', bytes: 45324 },
  'script_vocab.json': { sha256: '9be9f820e7abdc498b307ea56ffadddb5e37149e89f7cef037ff3929644dec4b', bytes: 731 },
};

const S = {
  base: null, symBase: null, manifest: null,
  keys: null, skeys: null,               // distinct keys and their ASCII-folded forms
  k: null, gid: null, pgid: null, kind: null, text: null,   // name rows, sorted by k
  nEmbedded: 0, emb: null,
  loading: null, phonLoading: null, ort: null, session: null, tokenise: null, vocabs: null,
};

const PUNCT = /[()\[\]{}.,;:!?"“”‘’*+\/\\|<>=_~`^#%&@]/g;
export function nameKey(text) {
  return String(text || '').normalize('NFC').replace(PUNCT, '').replace(/-/g, ' ').replace(/'/g, '')
    .replace(/\s+/g, ' ').trim().toLowerCase();
}
const FOLD = [[/æ/g, 'ae'], [/œ/g, 'oe'], [/þ/g, 'th'], [/ð/g, 'th'], [/ø/g, 'o'], [/ß/g, 'ss'], [/ł/g, 'l'], [/đ/g, 'd']];
export function asciiFold(s) {
  let t = s.normalize('NFD').replace(/\p{M}+/gu, '').toLowerCase();
  for (const [re, r] of FOLD) t = t.replace(re, r);
  return t;
}

const post = (m, transfer) => self.postMessage(m, transfer || []);

async function load(manifest, base) {
  if (S.loading) return S.loading;
  S.loading = (async () => {
    S.base = base;
    S.manifest = manifest;
    const nm = manifest.names;
    let done = 0;
    const total = nm.keys.bytes + nm.shards.reduce((a, s) => a + s.bytes, 0);
    const prog = () => post({ type: 'progress', what: 'index', loaded: done, total });
    const keys = await cached(nm.keys.file, nm.keys.sha256, { base, onProgress: (l) => { done = l; prog(); } });
    const kb = nm.keys.bytes;
    const parts = [];
    let off = kb;
    for (const sh of nm.shards) {
      const o = off;
      parts.push(await cached(sh.file, sh.sha256, { base, onProgress: (l) => { done = o + l; prog(); } }));
      off += sh.bytes;
    }
    const n = parts.reduce((a, p) => a + p.k.length, 0);
    S.keys = keys;
    S.nEmbedded = nm.nEmbedded;
    S.k = new Int32Array(n); S.gid = new Int32Array(n); S.pgid = new Int32Array(n); S.kind = new Uint8Array(n);
    S.text = new Array(n);
    let i = 0;
    for (const p of parts) {
      S.k.set(p.k, i); S.gid.set(p.gid, i); S.pgid.set(p.pgid, i); S.kind.set(p.kind, i);
      for (let j = 0; j < p.text.length; j++) S.text[i + j] = p.text[j];
      i += p.k.length;
    }
    S.skeys = new Array(keys.length);
    for (let j = 0; j < keys.length; j++) S.skeys[j] = asciiFold(keys[j]);
    // word -> embedded key indices, so a query word can fetch every multi-word key that contains it
    // ("bunsty" -> "bunsty hundred", "bunsty farm") even when the whole-string cosine is low
    S.wordIndex = new Map();
    for (let j = 0; j < S.nEmbedded; j++) {
      for (const w of S.skeys[j].split(' ')) {
        if (w.length < 2) continue;
        const a = S.wordIndex.get(w);
        if (a) a.push(j); else S.wordIndex.set(w, [j]);
      }
    }
    post({ type: 'index-ready', nKeys: keys.length, nRows: n });
  })();
  S.loading.catch(() => { S.loading = null; });
  return S.loading;
}

/* Rows of key index `ki`: [lo, hi) in the k-sorted arrays. */
function rowRange(ki) {
  const k = S.k;
  let lo = 0, hi = k.length;
  while (lo < hi) { const m = (lo + hi) >> 1; if (k[m] < ki) lo = m + 1; else hi = m; }
  const start = lo;
  hi = k.length;
  while (lo < hi) { const m = (lo + hi) >> 1; if (k[m] <= ki) lo = m + 1; else hi = m; }
  return [start, lo];
}
function rowsOf(ki, cap = 40) {
  const [a, b] = rowRange(ki);
  const out = [];
  for (let r = a; r < b && out.length < cap; r++) out.push({ gid: S.gid[r], pgid: S.pgid[r], kind: S.kind[r], text: S.text[r] });
  return { rows: out, n: b - a };
}

function textSearch(q, limit) {
  const fq = asciiFold(nameKey(q));
  if (!fq) return [];
  const words = fq.split(' ');
  const hits = [];
  const sk = S.skeys;
  for (let j = 0; j < sk.length; j++) {
    const s = sk[j];
    const at = s.indexOf(fq);
    let score;
    if (at === 0) score = s.length === fq.length ? 0 : 1;
    else if (at > 0) score = s.charCodeAt(at - 1) === 32 ? 2 : 3;
    else if (words.length > 1 && words.every((w) => s.includes(w))) score = 4;
    else continue;
    hits.push([score, s.length, j]);
    if (hits.length > 5000) break;
  }
  hits.sort((a, b) => a[0] - b[0] || a[1] - b[1] || (S.keys[a[2]] < S.keys[b[2]] ? -1 : 1));
  return hits.slice(0, limit).map(([score, , j]) => ({ key: S.keys[j], ki: j, score, ...rowsOf(j) }));
}

/* ── phonetic ─────────────────────────────────────────────────────────────────────────────── */
const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
async function fetchVerified(name) {
  const a = ASSETS[name];
  const res = await fetch(new URL(`${name}?v=${a.sha256.slice(0, 16)}`, S.symBase));
  if (!res.ok) throw new Error(`${name} did not load (HTTP ${res.status})`);
  const buf = await res.arrayBuffer();
  if (!crypto?.subtle) throw new Error('this browser cannot verify the model files, so phonetic matching is off');
  const got = hex(await crypto.subtle.digest('SHA-256', buf));
  if (got !== a.sha256) throw new Error(`${name} is not the Symphonym v8 file (sha256 ${got.slice(0, 16)}…); refusing to run`);
  return buf;
}

async function loadPhonetic() {
  if (S.phonLoading) return S.phonLoading;
  S.phonLoading = (async () => {
    const sm = await (await fetch(new URL('symphonym/manifest.json', S.base), { cache: 'no-cache' })).json();
    if (sm.n !== S.nEmbedded || sm.dim !== DIM) throw new Error(`phonetic matrix (${sm.n}) does not match the name index (${S.nEmbedded}); rebuild the site data`);
    if (sm.model_md5 !== 'afc74f102d9ab98e92382dcaed93628c') throw new Error('phonetic matrix was not built with Symphonym v8');
    let done = 0;
    const total = sm.shards.reduce((a, s) => a + s.bytes, 0) + ASSETS['symphonym.onnx'].bytes + ASSETS['char_vocab.json'].bytes;
    const prog = () => post({ type: 'progress', what: 'phonetic', loaded: done, total });
    const emb = new Int8Array(sm.n * DIM);
    let off = 0, base = 0;
    for (const sh of sm.shards) {
      const o = base;
      const buf = await cached('symphonym/' + sh.file, sh.sha256, { base: S.base, binary: true, onProgress: (l) => { done = o + l; prog(); } });
      emb.set(new Int8Array(buf), off);
      off += sh.rows * DIM; base += sh.bytes;
    }
    S.emb = emb;
    S.lang = sm.lang || 'en';
    const [ort, pre, cv, sv, lv, onnx] = await Promise.all([
      import(ORT_DIST + 'ort.wasm.min.mjs'),
      import('../symphonym/preprocess.js'),
      ...['char_vocab.json', 'script_vocab.json', 'lang_vocab.json', 'symphonym.onnx'].map(fetchVerified),
    ]);
    done = total; prog();
    ort.env.wasm.wasmPaths = { wasm: ORT_DIST + 'ort-wasm-simd-threaded.wasm', mjs: ORT_DIST + 'ort-wasm-simd-threaded.mjs' };
    ort.env.wasm.numThreads = 1;
    const json = (b) => JSON.parse(new TextDecoder().decode(b));
    const c = json(cv), s = json(sv), l = json(lv);
    S.vocabs = { charToId: c.char_to_id || c, scriptToId: s.script_to_id || s, langToId: l.lang_to_id || l };
    S.session = await ort.InferenceSession.create(new Uint8Array(onnx), { executionProviders: ['wasm'] });
    S.ort = ort; S.tokenise = pre.tokenise;
    post({ type: 'phonetic-ready', n: sm.n });
  })();
  S.phonLoading.catch(() => { S.phonLoading = null; S.emb = null; });
  return S.phonLoading;
}

async function embed(text) {
  const t = S.tokenise(text, S.lang || 'en', S.vocabs);   // the corpus tag, read from its manifest
  const n = t.charIds.length;
  const i64 = (v) => BigInt64Array.from([BigInt(v)]);
  const out = await S.session.run({
    char_ids: new S.ort.Tensor('int64', BigInt64Array.from(t.charIds, (v) => BigInt(v)), [1, n]),
    script_id: new S.ort.Tensor('int64', i64(t.scriptId), [1]),
    lang_id: new S.ort.Tensor('int64', i64(t.langId), [1]),
    length: new S.ort.Tensor('int64', i64(n), [1]),
  });
  return out.embedding.data;   // Float32Array(128), L2-normalised
}

/* Sørensen–Dice over character bigrams with whg3's word-containment lift (reconciliation.js
   nameSimilarity), on the ASCII-folded keys. */
const bigrams = (s) => { const out = new Set(); for (let i = 0; i < s.length - 1; i++) out.add(s.slice(i, i + 2)); return out; };
function nameSimilarity(x, y) {
  if (!x || !y) return 0;
  if (x === y) return 1;
  const xt = x.split(' '), yt = y.split(' ');
  const short = xt.length <= yt.length ? xt : yt, long = new Set(xt.length <= yt.length ? yt : xt);
  if (short.every((t) => long.has(t))) return 1;
  const A = bigrams(x), B = bigrams(y);
  if (!A.size || !B.size) return 0;
  let common = 0;
  A.forEach((g) => { if (B.has(g)) common += 1; });
  return (2 * common) / (A.size + B.size);
}

async function phoneticSearch(q, limit) {
  await loadPhonetic();
  const fq = asciiFold(nameKey(q));
  const v = await embed(nameKey(q));
  const emb = S.emb, n = S.nEmbedded;
  const cos = new Float32Array(n);
  for (let r = 0; r < n; r++) {
    let s = 0;
    const o = r * DIM;
    for (let d = 0; d < DIM; d++) s += v[d] * emb[o + d];
    cos[r] = s / 127;                        // ≈ cosine: the corpus is unit vectors scaled by 127
  }
  // candidates: the phonetic top-K, plus every embedded key sharing a whole word with the query
  const cand = new Set();
  const K = PHON_TOPK, bi = new Int32Array(K).fill(-1), bs = new Float32Array(K).fill(-Infinity);
  for (let r = 0; r < n; r++) {
    const s = cos[r];
    if (s > bs[K - 1]) {
      let j = K - 1;
      while (j > 0 && bs[j - 1] < s) { bs[j] = bs[j - 1]; bi[j] = bi[j - 1]; j--; }
      bs[j] = s; bi[j] = r;
    }
  }
  for (let i = 0; i < K; i++) if (bi[i] >= 0) cand.add(bi[i]);
  for (const w of fq.split(' ')) for (const j of S.wordIndex.get(w) || []) cand.add(j);
  const scored = [];
  for (const j of cand) {
    if (cos[j] < COS_MIN) continue;
    const ortho = nameSimilarity(fq, S.skeys[j]);
    if (ortho < DICE_VETO) continue;                      // the veto: sounds alike, but is not spelt alike at all
    scored.push({ ki: j, cos: cos[j], ortho });
  }
  scored.sort((a, b) => b.cos - a.cos || b.ortho - a.ortho);
  return scored.slice(0, limit).map((h) => ({ key: S.keys[h.ki], ki: h.ki, score: h.cos, cos: h.cos, ortho: h.ortho, ...rowsOf(h.ki) }));
}

self.onmessage = async ({ data: m }) => {
  try {
    if (m.type === 'init') {
      S.symBase = m.symBase;
      await load(m.manifest, m.base);
      post({ type: 'ready', id: m.id });
    } else if (m.type === 'search') {
      await S.loading;
      post({ type: 'results', id: m.id, q: m.q, hits: textSearch(m.q, m.limit || 30) });
    } else if (m.type === 'phonetic') {
      await S.loading;
      post({ type: 'phonetic-results', id: m.id, q: m.q, hits: await phoneticSearch(m.q, m.limit || 12) });
    } else if (m.type === 'warm-phonetic') {
      await S.loading;
      await loadPhonetic();
      post({ type: 'phonetic-ready', id: m.id, n: S.nEmbedded });
    } else if (m.type === 'rows') {           // all name rows for one place (for the detail view)
      await S.loading;
      const out = [];
      for (let r = 0; r < S.gid.length; r++) if (S.gid[r] === m.gid) out.push({ kind: S.kind[r], text: S.text[r], key: S.keys[S.k[r]] });
      post({ type: 'rows', id: m.id, gid: m.gid, rows: out });
    }
  } catch (err) {
    post({ type: 'error', id: m.id, error: String(err?.message || err) });
  }
};

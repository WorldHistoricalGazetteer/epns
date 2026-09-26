/* app.js — the DEEP explorer: map, search, browse, and the place page.
 *
 * Data comes from docs/data/ as built by process/export_site.py (see its docstring for the file
 * shapes) through the IndexedDB cache in store.js; search runs in search.worker.js. The page keeps
 * two invariants worth knowing before changing it:
 *
 *  - Every place is addressed by its dense integer gid. The 161k non-field-name places are in
 *    memory from boot (core.json); a field-name is reachable only through its county file, which
 *    is fetched when first needed. `rowOf.has(gid)` is therefore the test for "is this a
 *    field-name", and the manifest's per-county gid ranges say which file holds one.
 *  - `window.deep` is the page's own readiness flag for the headless checks in tools/pages/shot.py:
 *    `ready` goes true once the core index is loaded AND the map has gone idle once, and `renders`
 *    is bumped after every drawer render. Wait on those, never on MapLibre's events or the network.
 */
import { CONFIG } from './config.js?v=1';
import { cached, clearAll, usage } from './store.js?v=1';

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmt = (n) => Number(n).toLocaleString('en-GB');
const DATA = new URL('data/', location.href).href;
const SYM = new URL('symphonym/', location.href).href;

window.deep = { ready: false, renders: 0, coreLoaded: false, mapIdle: false, map: null, state: null };

const S = {
  manifest: null, core: null, rowOf: new Map(), kids: new Map(), byId: new Map(), counties: [],
  shards: new Map(), shardLoading: new Map(),
  worker: null, pending: new Map(), seq: 0, qTok: 0, indexReady: false, phonReady: false, phonBusy: false,
  current: null, lastResults: null, map: null, selectedGid: -1, basemap: null,
};
window.deep.state = S;

/* ── vocabulary ─────────────────────────────────────────────────────────────────────────── */
const TYPE_LABEL = {
  county: 'county', province: 'province', subcounty: 'hundred / wapentake', dbhundred: 'Domesday hundred',
  halfhundred: 'half-hundred', liberty: 'liberty', abovesubcounty: 'division', belowsubcounty: 'sub-division of a hundred',
  localdistrict: 'local district', parish: 'parish', borough: 'borough', countytown: 'county town',
  subparish: 'township', chapelry: 'chapelry', belowparish: 'sub-parish', mappedname: 'minor name', fn: 'field-name',
  forest: 'forest', feature: 'feature',
};
const AUTH_LABEL = { histfn: 'historical field-name', modfn: 'modern field-name', histmappedname: 'historical minor name' };
const GROUP = {
  parish: 'parish', borough: 'parish', countytown: 'parish', subparish: 'township', chapelry: 'township', belowparish: 'township',
  mappedname: 'name', fn: 'fn', county: 'county', province: 'county',
};
const groupOf = (t) => GROUP[t] || 'admin';
/* Rank for ordering equal-quality search hits: the larger the unit, the higher. A query that matches
   a hundred's headword and a farm's equally should show the hundred first; field-names last. */
const TYPE_RANK = { county: 0, province: 0, countytown: 1, borough: 1, subcounty: 2, dbhundred: 2, halfhundred: 2, liberty: 2, abovesubcounty: 2,
  belowsubcounty: 3, localdistrict: 3, parish: 4, subparish: 5, chapelry: 5, belowparish: 5, forest: 6, feature: 6, mappedname: 7, fn: 9 };
const GROUP_COLOUR = { parish: '#c0392b', township: '#d98c2b', name: '#2a6f97', admin: '#6b4a8a', county: '#6f6f6f', fn: '#6f6f6f' };

/* ── core index ─────────────────────────────────────────────────────────────────────────── */
const C = () => S.core;
const typeName = (i) => C().types[C().type[i]];
const title = (i) => C().title[i];
const countyOf = (i) => S.counties[C().county[i]];
const deepIdOf = (i) => `epns-deep-${countyOf(i)?.code}-${C().code[i]}-${typeName(i)}-${String(C().seq[i]).padStart(6, '0')}`;

function indexCore() {
  const c = C();
  for (let i = 0; i < c.gid.length; i++) {
    S.rowOf.set(c.gid[i], i);
    const p = c.parent[i];
    if (p >= 0) { const arr = S.kids.get(p); if (arr) arr.push(i); else S.kids.set(p, [i]); }
    S.byId.set(`${c.counties[c.county[i]]?.code}-${c.code[i]}-${c.types[c.type[i]]}-${c.seq[i]}`, c.gid[i]);
  }
  S.counties = c.counties;
  for (const arr of S.kids.values()) arr.sort((a, b) => (c.title[a] || '').localeCompare(c.title[b] || ''));
}

function countyCodeForGid(gid) {
  const i = S.rowOf.get(gid);
  if (i !== undefined) return countyOf(i)?.code;
  const m = S.manifest.counties.find((f) => gid >= f.gidFrom && gid <= f.gidTo);
  return m?.code;
}

async function shardFor(code) {
  if (S.shards.has(code)) return S.shards.get(code);
  if (S.shardLoading.has(code)) return S.shardLoading.get(code);
  const info = S.manifest.counties.find((f) => f.code === code);
  if (!info) throw new Error(`no county file for ${code}`);
  const p = (async () => {
    setStatus(`Loading ${esc(info.name)} (${(info.bytes / 1e6).toFixed(1)} MB)…`, 0);
    const data = await cached(info.file, info.sha256, { base: DATA, onProgress: (l, t) => setStatus(`Loading ${esc(info.name)}…`, t ? l / t : 0) });
    const byGid = new Map(), kidsFn = new Map();
    for (const r of data.places) {
      byGid.set(r.g, r);
      if (data && C().types[r.ty] === 'fn') { const a = kidsFn.get(r.p); if (a) a.push(r); else kidsFn.set(r.p, [r]); }
    }
    for (const a of kidsFn.values()) a.sort((x, y) => x.t.localeCompare(y.t));
    const sh = { ...data, byGid, kidsFn };
    S.shards.set(code, sh);
    S.shardLoading.delete(code);
    setStatus('');
    return sh;
  })();
  S.shardLoading.set(code, p);
  return p;
}

/* ── status / veil ──────────────────────────────────────────────────────────────────────── */
function setStatus(html, frac) {
  const el = $('status');
  el.innerHTML = html ? html + (frac != null ? `<span class="bar"><i style="width:${Math.round(frac * 100)}%"></i></span>` : '') : '';
}
function veil(msg, frac) {
  $('veil-msg').textContent = msg;
  $('veil-bar').style.width = `${Math.round((frac || 0) * 100)}%`;
}
function maybeReady() {
  if (window.deep.coreLoaded && window.deep.mapIdle && !window.deep.ready) {
    window.deep.ready = true;
    $('veil').hidden = true;
  }
}

/* ── map ────────────────────────────────────────────────────────────────────────────────── */
const BASEMAPS = [
  { id: 'carto', label: 'CARTO light', needs: 'cartoKey', tileSize: 512, maxzoom: 20,
    tiles: (k) => ['a', 'b', 'c'].map((h) => `https://${h}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png?key=${encodeURIComponent(k)}`),
    attribution: '&copy; <a href="https://carto.com/attributions">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' },
  { id: 'osm', label: 'OpenStreetMap', tileSize: 256, maxzoom: 19, tiles: () => ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' },
  { id: 'nls', label: 'OS six-inch, 1888–1913', tileSize: 256, maxzoom: 17, minzoom: 1,
    tiles: () => ['https://mapseries-tilesets.s3.amazonaws.com/os/6inchsecond/{z}/{x}/{y}.png'],
    attribution: 'Historic maps &copy; <a href="https://maps.nls.uk">National Library of Scotland</a>, CC BY 3.0' },
].filter((b) => !b.needs || CONFIG[b.needs]);

const DATA_ATTRIBUTION = 'Place-name survey data &copy; <a href="https://www.nottingham.ac.uk/research/groups/epns/">English Place-Name Society</a>, digitised by <a href="http://mads.digitalresources.jisc.ac.uk/mads2017/">DEEP / Jisc</a>, <a href="https://creativecommons.org/licenses/by-nc/4.0/">CC BY-NC 4.0</a>';

class BasemapControl {
  onAdd(map) {
    this._map = map;
    const el = document.createElement('div');
    el.className = 'maplibregl-ctrl basemaps';
    el.innerHTML = '<b>Basemap</b>' + BASEMAPS.map((b) => `<label><input type="radio" name="basemap" value="${b.id}" ${b.id === S.basemap ? 'checked' : ''}> ${b.label}</label>`).join('');
    el.addEventListener('change', (e) => setBasemap(e.target.value));
    this._el = el;
    return el;
  }
  onRemove() { this._el.remove(); this._map = null; }
}

function setBasemap(id) {
  const b = BASEMAPS.find((x) => x.id === id) || BASEMAPS[0];
  const map = S.map;
  if (map.getLayer('basemap')) map.removeLayer('basemap');
  if (map.getSource('basemap')) map.removeSource('basemap');
  map.addSource('basemap', { type: 'raster', tiles: b.tiles(CONFIG[b.needs]), tileSize: b.tileSize, maxzoom: b.maxzoom, minzoom: b.minzoom || 0, attribution: b.attribution });
  map.addLayer({ id: 'basemap', type: 'raster', source: 'basemap' }, map.getLayer('places') ? 'places' : undefined);
  S.basemap = b.id;
  try { localStorage.setItem('deep_basemap', b.id); } catch { /* fine */ }
  const radio = document.querySelector(`input[name=basemap][value=${b.id}]`);
  if (radio) radio.checked = true;
}

function pointsGeoJSON() {
  const c = C(), feats = [];
  for (let i = 0; i < c.gid.length; i++) {
    if (c.geo[i] !== 1) continue;
    feats.push({ type: 'Feature', geometry: { type: 'Point', coordinates: [c.lon[i], c.lat[i]] },
                 properties: { gid: c.gid[i], g: groupOf(c.types[c.type[i]]) } });
  }
  return { type: 'FeatureCollection', features: feats };
}

function initMap() {
  let saved = null;
  try { saved = localStorage.getItem('deep_basemap'); } catch { /* fine */ }
  S.basemap = BASEMAPS.some((b) => b.id === saved) ? saved : BASEMAPS[0].id;
  const map = new maplibregl.Map({
    container: 'map', style: { version: 8, sources: {}, layers: [] },
    center: [-1.6, 52.7], zoom: 6.2, minZoom: 4, maxZoom: 18, attributionControl: false, hash: false,
  });
  S.map = map; window.deep.map = map;
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
  map.addControl(new BasemapControl(), 'top-right');
  map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-right');
  map.addControl(new maplibregl.AttributionControl({ compact: false, customAttribution: DATA_ATTRIBUTION }), 'bottom-right');
  map.on('load', () => {
    setBasemap(S.basemap);
    map.addSource('places', { type: 'geojson', data: pointsGeoJSON() });
    map.addLayer({
      id: 'places', type: 'circle', source: 'places',
      paint: {
        'circle-color': ['match', ['get', 'g'], 'parish', GROUP_COLOUR.parish, 'township', GROUP_COLOUR.township, 'name', GROUP_COLOUR.name, 'admin', GROUP_COLOUR.admin, GROUP_COLOUR.county],
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 5, 1.6, 8, 3, 11, 5, 14, 7],
        'circle-stroke-color': '#fff', 'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 6, 0.3, 11, 1],
        'circle-opacity': ['interpolate', ['linear'], ['zoom'], 5, 0.75, 9, 0.95],
      },
    });
    map.addLayer({
      id: 'selected', type: 'circle', source: 'places', filter: ['==', ['get', 'gid'], -1],
      paint: { 'circle-color': 'rgba(0,0,0,0)', 'circle-radius': ['interpolate', ['linear'], ['zoom'], 5, 7, 12, 14], 'circle-stroke-color': '#1d2433', 'circle-stroke-width': 2.5 },
    });
    const tip = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8, className: 'tip' });
    map.on('mousemove', 'places', (e) => {
      map.getCanvas().style.cursor = 'pointer';
      const f = e.features[0]; const i = S.rowOf.get(f.properties.gid);
      if (i === undefined) return;
      tip.setLngLat(f.geometry.coordinates).setHTML(`<span class="tip-name">${esc(title(i))}</span><span class="tip-ctx">${esc(TYPE_LABEL[typeName(i)] || typeName(i))} · ${esc(countyOf(i)?.name)}</span>`).addTo(map);
    });
    map.on('mouseleave', 'places', () => { map.getCanvas().style.cursor = ''; tip.remove(); });
    map.on('click', 'places', (e) => { tip.remove(); openPlace(e.features[0].properties.gid, { fly: false }); });
    map.once('idle', () => { window.deep.mapIdle = true; maybeReady(); });
  });
}

function selectOnMap(gid) {
  S.selectedGid = gid;
  if (S.map.getLayer('selected')) S.map.setFilter('selected', ['==', ['get', 'gid'], gid]);
}

/* ── worker ─────────────────────────────────────────────────────────────────────────────── */
function startWorker() {
  const w = new Worker('js/search.worker.js?v=1', { type: 'module' });
  S.worker = w;
  w.onmessage = ({ data: m }) => {
    if (m.type === 'progress') {
      const mb = (x) => (x / 1e6).toFixed(1);
      if (m.what === 'index') setStatus(`Loading the name index… ${mb(m.loaded)} / ${mb(m.total)} MB`, m.total ? m.loaded / m.total : 0);
      else setStatus(`Loading the phonetic model and index… ${mb(m.loaded)} / ${mb(m.total)} MB`, m.total ? m.loaded / m.total : 0);
      return;
    }
    if (m.type === 'index-ready') { S.indexReady = true; setStatus(''); const q = $('q').value.trim(); if (q.length >= 2) doSearch(q); return; }
    if (m.type === 'phonetic-ready') { S.phonReady = true; S.phonBusy = false; setStatus(''); }
    const p = m.id != null ? S.pending.get(m.id) : null;
    if (p) { S.pending.delete(m.id); m.type === 'error' ? p.reject(new Error(m.error)) : p.resolve(m); }
    else if (m.type === 'error') { setStatus(`<span class="warn">${esc(m.error)}</span>`); console.error('[deep worker]', m.error); }
  };
  w.onerror = (e) => { setStatus(`<span class="warn">search unavailable: ${esc(e.message)}</span>`); console.error(e); };
  call({ type: 'init', manifest: S.manifest, base: DATA, symBase: SYM }).catch((e) => setStatus(`<span class="warn">${esc(e.message)}</span>`));
}
function call(msg) {
  const id = ++S.seq;
  return new Promise((resolve, reject) => { S.pending.set(id, { resolve, reject }); S.worker.postMessage({ ...msg, id }); });
}

/* ── search ─────────────────────────────────────────────────────────────────────────────── */
const KIND_WORD = ['', 'spelling of', 'normalised form of'];

function rowContext(row) {
  const i = S.rowOf.get(row.gid);
  if (i !== undefined) {
    const p = C().parent[i], pi = p >= 0 ? S.rowOf.get(p) : undefined;
    return { name: title(i), type: TYPE_LABEL[typeName(i)] || typeName(i), parent: pi !== undefined ? title(pi) : '', county: countyOf(i)?.name || '', fn: false };
  }
  const pi = S.rowOf.get(row.pgid);
  return { name: row.kind === 0 ? row.text : '', type: 'field-name', parent: pi !== undefined ? title(pi) : '', county: pi !== undefined ? countyOf(pi)?.name || '' : '', fn: true };
}

function resultRow(row, score) {
  const ctx = rowContext(row);
  const what = row.kind === 0 ? ctx.type : `${KIND_WORD[row.kind]} <b>${esc(ctx.name)}</b>, ${esc(ctx.type)}`;
  const where = [ctx.parent, ctx.county].filter(Boolean).map(esc).join(' · ');
  return `<li data-gid="${row.gid}">${score != null ? `<span class="score">${score.toFixed(2)}</span>` : ''}<span class="form">${esc(row.text)}</span> <span class="ctx">${what}${where ? ' · ' + where : ''}</span></li>`;
}

function flattenHits(hits, includeFn, phonetic = false) {
  const seen = new Set(), out = [];
  for (const h of hits) {
    for (const r of h.rows) {
      if (!includeFn && !S.rowOf.has(r.gid)) continue;
      const k = `${r.gid}|${r.text}`;
      if (seen.has(k)) continue;
      seen.add(k);
      const i = S.rowOf.get(r.gid);
      out.push({ ...r, score: h.score, rank: i === undefined ? TYPE_RANK.fn : (TYPE_RANK[typeName(i)] ?? 8), len: h.key.length });
    }
  }
  // text hits: match quality, then size of unit, then shorter key; phonetic hits: score only
  out.sort(phonetic ? (a, b) => b.score - a.score : (a, b) => a.score - b.score || a.rank - b.rank || a.len - b.len || a.kind - b.kind);
  return out;
}

async function doSearch(q) {
  q = q.trim();
  const tok = ++S.qTok;
  if (q.length < 2) { if (!S.current) hideDrawer(); return; }
  if (!S.indexReady) { showDrawer(`<div class="rs-status">Loading the name index…</div>`); return; }
  const includeFn = $('fn').checked, phonOn = $('phon').checked;
  let res;
  try { res = await call({ type: 'search', q, limit: 40 }); } catch (e) { showDrawer(`<div class="rs-status warn">${esc(e.message)}</div>`); return; }
  if (tok !== S.qTok) return;
  const rows = flattenHits(res.hits, includeFn).slice(0, 60);
  let h = `<button class="close" title="Close">×</button>`;
  h += phonOn ? `<div class="rs-status" id="phon-pending">${S.phonReady ? 'matching by sound…' : 'loading the phonetic model on first use…'}</div>` : '';
  h += rows.length ? `<div class="rs-sec">Names and spellings</div><ul class="rs">${rows.map((r) => resultRow(r)).join('')}</ul>`
                   : `<div class="rs-status">No spelling matches “${esc(q)}”${phonOn ? '' : ' — try phonetic matching for how it sounds'}.</div>`;
  S.current = null;
  S.lastResults = h;
  showDrawer(h);
  if (!phonOn) return;
  if (!S.phonReady) S.phonBusy = true;
  call({ type: 'phonetic', q, limit: 15 }).then((pr) => {
    if (tok !== S.qTok) return;
    const pend = $('phon-pending'); if (pend) pend.remove();
    const prow = flattenHits(pr.hits, includeFn, true).filter((r) => !rows.some((x) => x.gid === r.gid && x.text === r.text)).slice(0, 30);
    if (prow.length) $('drawer').insertAdjacentHTML('afterbegin', `<div class="rs-sec">Sounds like “${esc(q)}”</div><ul class="rs">${prow.map((r) => resultRow(r, r.score)).join('')}</ul>`);
    else $('drawer').insertAdjacentHTML('afterbegin', `<div class="rs-status">Nothing sounds close enough to “${esc(q)}” (cosine ≥ 0.70).</div>`);
    S.lastResults = $('drawer').innerHTML;
    window.deep.renders++;
  }).catch((e) => { if (tok !== S.qTok) return; const pend = $('phon-pending'); if (pend) pend.outerHTML = `<div class="rs-status warn">phonetic matching unavailable: ${esc(e.message)}</div>`; });
}

/* ── drawer ─────────────────────────────────────────────────────────────────────────────── */
function positionDrawer() {
  const pane = $('pane');
  $('drawer').style.top = `${pane.offsetTop + pane.offsetHeight + 8}px`;
  $('drawer').style.maxHeight = `calc(100vh - ${pane.offsetTop + pane.offsetHeight + 20}px)`;
}
function showDrawer(html) {
  const d = $('drawer');
  d.innerHTML = html;
  d.hidden = false;
  d.scrollTop = 0;
  positionDrawer();
  window.deep.renders++;
}
function hideDrawer() { $('drawer').hidden = true; S.current = null; selectOnMap(-1); if (location.hash) history.replaceState(null, '', location.pathname + location.search); }

/* ── place page ─────────────────────────────────────────────────────────────────────────── */
function crumbsFor(i) {
  const c = C(), parts = [];
  const push = (gid) => { const j = S.rowOf.get(gid); if (j !== undefined && j !== i) parts.push(`<a href="#id=${shortId(j)}" data-gid="${gid}">${esc(title(j))}</a>`); };
  const co = countyOf(i);
  if (co) push(co.gid);
  for (const k of ['hundred', 'parish', 'township']) { const g = c[k][i]; if (g >= 0) push(g); }
  // a place whose parent is none of the standard levels (a liberty inside a parish, say) still shows it
  const p = c.parent[i];
  if (p >= 0 && !parts.some((s) => s.includes(`data-gid="${p}"`))) push(p);
  return parts.join('<span class="sep">›</span>');
}
const shortId = (i) => `${countyOf(i)?.code}-${C().code[i]}-${typeName(i)}-${String(C().seq[i]).padStart(6, '0')}`;
const shortIdRec = (rec, code) => `${code}-${rec.code}-${C().types[rec.ty]}-${String(rec.seq).padStart(6, '0')}`;

function renderAtt(a, rec) {
  const dates = (a.d || []).map((d) => esc(d[3])).join(', ');
  const copy = a.c && a.c[0] ? ` <span class="ap">(${esc(a.c[0])})</span>` : '';
  const src = a.s ? `<span class="src${a.s[2] ? ' it' : ''}" title="${esc(a.s[0] || '')}">${esc(a.s[1] || '')}</span>` : '';
  const x = a.x || {};
  const ap = [x.page, x.item, x.folio ? `f. ${x.folio}` : null, x.entry, x.appendix ? `app. ${x.appendix}` : null, x.note ? `n. ${x.note}` : null, x.number,
              x.ms ? `(${x.ms})` : null, x.pername, x.times].filter(Boolean).map(esc).join(' ');
  const u = a.u ? ` <span class="u" title="the volume gives the source as ibidem / idem; resolved editorially">${a.u}.</span>` : '';
  return `<span class="att">${dates}${copy} ${src}${ap ? ' <span class="ap">' + ap + '</span>' : ''}${u}</span>`;
}

function renderForms(rec) {
  if (!rec.v || !rec.v.length) return '';
  const atts = (rec.a || []).slice().sort((p, q) => p.p - q.p);
  const passim = (rec.ps || []).slice().sort((p, q) => p[0] - q[0]);
  const items = rec.v.map(([vid, text]) => {
    const mine = atts.filter((a) => (a.v || []).includes(vid));
    const parts = [];
    let prev = -1;
    for (const a of mine) {
      for (const [pos, ptext] of passim) if (pos > prev && pos < a.p && prev >= 0) parts.push(`<span class="passim">${esc(ptext)}</span>`);
      parts.push(renderAtt(a, rec));
      prev = a.p;
    }
    return `<li><b>${esc(text)}</b> ${parts.join(', ')}</li>`;
  });
  return `<h3>Spellings and attestations</h3><ul class="forms">${items.join('')}</ul>`;
}

function renderGeo(rec, i) {
  const rows = rec.geo || [];
  if (!rows.length) {
    if (i !== undefined && C().geo[i] === 2) return `<h3>Location</h3><p class="meta"><span class="badge inh">inherited</span> No coordinates of its own; the map position is its parent's.</p>`;
    return `<h3>Location</h3><p class="meta">No coordinates in the data.</p>`;
  }
  const li = rows.map(([src, lon, lat, e, n, ref, lraw, traw]) => {
    const pos = lon != null && lat != null ? `<a href="#" data-fly="${lon},${lat}">${lat.toFixed(5)}, ${lon.toFixed(5)}</a>` : `<span class="ap">unparseable: ${esc(lraw || '')} ${esc(traw || '')}</span>`;
    const osgb = e != null ? ` · E ${e} N ${n}` : '';
    const r = ref ? ` · <span class="ap">${esc(ref)}</span>` : '';
    return `<li><span class="badge">${esc(src)}</span> ${pos}${osgb}${r}</li>`;
  });
  return `<h3>Location <span class="badge own">own coordinates</span></h3><ul class="geo-list">${li.join('')}</ul><p class="small meta">Candidates from each gazetteer that supplied one; the map uses the first in the order EPNS, KEPN, GeoNames, Unlock.</p>`;
}

function renderKids(gid, shard) {
  const c = C();
  const kids = S.kids.get(gid) || [];
  let h = '';
  if (kids.length) {
    const byType = new Map();
    for (const j of kids) { const t = typeName(j); const a = byType.get(t); if (a) a.push(j); else byType.set(t, [j]); }
    for (const [t, arr] of byType) {
      h += `<h3>${esc(TYPE_LABEL[t] || t)}${arr.length > 1 ? 's' : ''} <span class="n">(${fmt(arr.length)})</span></h3><ul class="kids">`
        + arr.map((j) => `<li><a href="#id=${shortId(j)}" data-gid="${c.gid[j]}">${esc(title(j))}</a>${c.nVar[j] ? ` <span class="n">${c.nVar[j]} spelling${c.nVar[j] > 1 ? 's' : ''}</span>` : ''}${c.nKids[j] ? ` <span class="n">· ${fmt(c.nKids[j])} places</span>` : ''}${c.nFn[j] ? ` <span class="n">· ${fmt(c.nFn[j])} field-names</span>` : ''}</li>`).join('')
        + '</ul>';
    }
  }
  const fns = shard?.kidsFn.get(gid) || [];
  if (fns.length) {
    const groups = [['modfn', 'Modern field-names'], ['histfn', 'Historical field-names'], [undefined, 'Field-names']];
    for (const [at, label] of groups) {
      const arr = fns.filter((r) => (r.at || undefined) === at);
      if (!arr.length) continue;
      h += `<details class="fn" ${arr.length <= 40 ? 'open' : ''}><summary>${label} (${fmt(arr.length)})</summary><ul class="fnlist">`
        + arr.map((r) => `<li><a href="#id=${shortIdRec(r, shard.code)}" data-gid="${r.g}">${esc(r.t)}</a></li>`).join('') + '</ul></details>';
    }
  }
  return h;
}

async function openPlace(gid, { fly = true, push = true } = {}) {
  const i = S.rowOf.get(gid);
  const code = countyCodeForGid(gid);
  if (!code) return;
  let shard;
  try { shard = await shardFor(code); } catch (e) { showDrawer(`<div class="rs-status warn">${esc(e.message)}</div>`); return; }
  const rec = shard.byGid.get(gid);
  if (!rec) { showDrawer(`<div class="rs-status warn">No record for gid ${gid} in ${esc(shard.name)}.</div>`); return; }
  const tname = C().types[rec.ty];
  const isFn = tname === 'fn';
  const label = AUTH_LABEL[rec.at] || TYPE_LABEL[tname] || tname;
  const id = `epns-deep-${shortIdRec(rec, shard.code)}`;
  const pi = rec.p >= 0 ? S.rowOf.get(rec.p) : undefined;
  const crumbs = i !== undefined ? crumbsFor(i) : (pi !== undefined ? crumbsFor(pi) + `<span class="sep">›</span><a href="#id=${shortId(pi)}" data-gid="${rec.p}">${esc(title(pi))}</a>` : '');
  const badges = [];
  if (i !== undefined && C().geo[i] === 1) badges.push('<span class="badge own">located</span>');
  else if (i !== undefined && C().geo[i] === 2) badges.push('<span class="badge inh">location inherited</span>');
  let h = `<button class="close" title="Close">×</button>`;
  if (S.lastResults) h += `<div class="actions"><button id="back-results">‹ Back to results</button></div>`;
  h += `<div class="crumbs">${crumbs}</div><h2>${esc(rec.t)}</h2>`;
  h += `<div class="meta">${esc(label)}${badges.length ? ' ' + badges.join(' ') : ''}${rec.cr ? ` · record ${esc(rec.cr)}` : ''}</div><div class="id">${esc(id)}</div>`;
  h += renderForms(rec);
  if (rec.st && rec.st.length && !isFn) h += `<details><summary class="meta">Normalised search forms (${rec.st.length})</summary><p class="meta">${rec.st.map((s) => esc(s[1])).join(', ')}</p></details>`;
  if (rec.n && rec.n.length) h += `<h3>Note</h3>${rec.n.map((n) => `<p class="meta">${esc(n)}</p>`).join('')}`;
  h += renderGeo(rec, i);
  h += renderKids(gid, shard);
  const links = [];
  if (rec.cs) links.push(`<a href="${esc(rec.cs)}" target="_blank" rel="noopener">DEEP source record</a>`);
  links.push(`<a href="https://kepn.nottingham.ac.uk/" target="_blank" rel="noopener">Key to English Place-Names</a>`);
  links.push(`<a href="#" id="copy-link">Copy link to this place</a>`);
  h += `<div class="links">${links.join('')}</div>`;
  S.current = gid;
  showDrawer(h);
  if (push) history.replaceState(null, '', `#id=${shortIdRec(rec, shard.code)}`);
  const c = C();
  const li = i !== undefined ? i : pi;
  selectOnMap(li !== undefined && c.geo[li] === 1 ? c.gid[li] : -1);
  if (fly && li !== undefined && c.lon[li] != null) {
    S.map.flyTo({ center: [c.lon[li], c.lat[li]], zoom: Math.max(S.map.getZoom(), isFn || tname === 'mappedname' ? 12 : tname === 'county' ? 8 : 10), speed: 1.4 });
  } else if (fly && tname === 'county') {
    const pts = (S.kids.get(gid) || []).flatMap((j) => (c.lon[j] != null ? [[c.lon[j], c.lat[j]]] : []));
    if (pts.length) { const b = pts.reduce((bb, p) => bb.extend(p), new maplibregl.LngLatBounds(pts[0], pts[0])); S.map.fitBounds(b, { padding: 60, maxZoom: 9 }); }
  }
}

async function openById(short) {
  const m = /^(\d+)-([a-z0-9]+)-([a-z]+)-(\d+)$/.exec(short);
  if (!m) return false;
  const key = `${m[1]}-${m[2]}-${m[3]}-${parseInt(m[4], 10)}`;
  if (S.byId.has(key)) { await openPlace(S.byId.get(key)); return true; }
  const shard = await shardFor(m[1]).catch(() => null);
  if (!shard) return false;
  const rec = shard.places.find((r) => r.code === m[2] && C().types[r.ty] === m[3] && r.seq === parseInt(m[4], 10));
  if (!rec) return false;
  await openPlace(rec.g);
  return true;
}

/* ── UI wiring ──────────────────────────────────────────────────────────────────────────── */
function fillCounties() {
  const sel = $('nav-county');
  const list = S.counties.slice().sort((a, b) => a.name.localeCompare(b.name));
  for (const co of list) { const o = document.createElement('option'); o.value = co.gid; o.textContent = co.name; sel.appendChild(o); }
  sel.addEventListener('change', () => { if (sel.value !== '') { $('q').value = ''; S.lastResults = null; openPlace(+sel.value); sel.value = ''; } });
}

function fillStats() {
  const k = S.manifest.counts;
  $('stats').innerHTML = `<table>
    <tr><td>Places</td><td><b>${fmt(k.place)}</b></td><td>of which field-names</td><td>${fmt(k.place_fn)}</td></tr>
    <tr><td>Name forms</td><td><b>${fmt(k.name)}</b></td><td>normalised search forms</td><td>${fmt(k.searchterm)}</td></tr>
    <tr><td>Attestations</td><td><b>${fmt(k.attestation)}</b></td><td>dated ${k.date_min}–${k.date_max}</td><td>${fmt(k.attestation_date)} dates</td></tr>
    <tr><td>Source abbreviations</td><td><b>${fmt(k.source)}</b></td><td>county volumes</td><td>${S.counties.length}</td></tr>
    <tr><td>Places with coordinates</td><td><b>${fmt(k.place_located_own)}</b></td><td>coordinate candidates</td><td>${fmt(k.geo)}</td></tr></table>`;
  $('stat-own').textContent = fmt(k.place_located_own);
  $('built').textContent = S.manifest.built.slice(0, 10);
}

function wireUI() {
  const qi = $('q');
  let t;
  qi.addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => { doSearch(qi.value); if (qi.value.trim()) history.replaceState(null, '', `#q=${encodeURIComponent(qi.value.trim())}`); }, 220); });
  qi.addEventListener('keydown', (e) => { if (e.key === 'Escape') { qi.value = ''; hideDrawer(); } });
  $('fn').addEventListener('change', () => { if (qi.value.trim().length >= 2) doSearch(qi.value); });
  $('phon').addEventListener('change', () => {
    try { localStorage.setItem('deep_phon', $('phon').checked ? '1' : '0'); } catch { /* fine */ }
    if ($('phon').checked && !S.phonReady) { S.phonBusy = true; call({ type: 'warm-phonetic' }).catch((e) => { S.phonBusy = false; setStatus(`<span class="warn">${esc(e.message)}</span>`); $('phon').checked = false; }); }
    if (qi.value.trim().length >= 2) doSearch(qi.value);
  });
  try { if (localStorage.getItem('deep_phon') === '1') { $('phon').checked = true; } } catch { /* fine */ }
  if ($('phon').checked) call({ type: 'warm-phonetic' }).catch(() => { $('phon').checked = false; });

  const d = $('drawer');
  d.addEventListener('click', (e) => {
    const a = e.target.closest('a[data-gid], li[data-gid]');
    if (a) { e.preventDefault(); openPlace(+a.dataset.gid); return; }
    const f = e.target.closest('a[data-fly]');
    if (f) { e.preventDefault(); const [lon, lat] = f.dataset.fly.split(',').map(Number); S.map.flyTo({ center: [lon, lat], zoom: Math.max(S.map.getZoom(), 13) }); return; }
    if (e.target.closest('.close')) { hideDrawer(); return; }
    if (e.target.id === 'back-results') { S.current = null; showDrawer(S.lastResults); selectOnMap(-1); return; }
    if (e.target.id === 'copy-link') {
      e.preventDefault();
      navigator.clipboard?.writeText(location.href).then(() => { e.target.textContent = 'Link copied'; setTimeout(() => { e.target.textContent = 'Copy link to this place'; }, 1500); });
    }
  });

  const modal = $('info-modal');
  const closeInfo = () => { modal.hidden = true; document.body.style.overflow = ''; };
  const openInfo = () => { modal.hidden = false; document.body.style.overflow = 'hidden'; usage().then((u) => { $('cache-size').textContent = u ? `(${(u / 1e6).toFixed(0)} MB in use)` : ''; }); };
  $('info-btn').addEventListener('click', openInfo);
  modal.addEventListener('click', (e) => { if (e.target === modal || e.target.id === 'info-close') closeInfo(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !modal.hidden) closeInfo(); });
  $('clear-cache').addEventListener('click', async () => { await clearAll(); location.reload(); });
  try {
    if (!localStorage.getItem('deep_info_seen')) { openInfo(); localStorage.setItem('deep_info_seen', '1'); }
  } catch { /* private mode: show it every time, which is the right failure */ openInfo(); }

  window.addEventListener('hashchange', applyHash);
  window.addEventListener('resize', positionDrawer);
}

async function applyHash() {
  const h = new URLSearchParams(location.hash.replace(/^#/, ''));
  if (h.get('id')) { const ok = await openById(h.get('id')); if (!ok) showDrawer(`<div class="rs-status warn">No place with id ${esc(h.get('id'))}.</div>`); }
  else if (h.get('q')) { $('q').value = h.get('q'); doSearch(h.get('q')); }
}

/* ── boot ───────────────────────────────────────────────────────────────────────────────── */
async function boot() {
  try {
    veil('Loading the manifest…', 0);
    S.manifest = await (await fetch(DATA + 'manifest.json', { cache: 'no-cache' })).json();
    S.core = await cached(S.manifest.core.file, S.manifest.core.sha256, {
      base: DATA, onProgress: (l, t) => veil(`Loading the place index… ${(l / 1e6).toFixed(1)} MB`, t ? l / t : 0),
    });
    veil('Indexing…', 1);
    indexCore();
    window.deep.coreLoaded = true;
    fillCounties();
    fillStats();
    initMap();
    startWorker();
    wireUI();
    positionDrawer();
    maybeReady();
    applyHash();
  } catch (e) {
    veil(`Could not start: ${e.message}`, 0);
    console.error(e);
  }
}
boot();

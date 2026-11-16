#!/usr/bin/python3
"""Headless checks of the DEEP explorer. No browser window, no foreground tab.

    /usr/bin/python3 tools/pages/shot.py --serve                 # every check against docs/ on a loopback server
    /usr/bin/python3 tools/pages/shot.py --serve --check search
    /usr/bin/python3 tools/pages/shot.py --serve --prove-it-fails
    /usr/bin/python3 tools/pages/shot.py --url https://worldhistoricalgazetteer.github.io/epns/

Run from the repository root. Exits non-zero if any check fails. Screenshots go to
tools/pages/out/.

SYSTEM PYTHON, DELIBERATELY. playwright is installed for /usr/bin/python3 with its own bundled
chromium (~/.cache/ms-playwright), not in the project venv and not in requirements.txt: a
development instrument does not belong in the manifest of the build. It launches its OWN
browser with a fresh temporary profile, so nothing here can reach the browser Stephen is
reading his email in. The recipe is the one in ~/.claude/memory/playwright-maplibre-headless-testing.md,
by way of London_Customs_Accounts/tools/pages/shot.py.

WAIT ON THE PAGE'S OWN FLAG. `window.deep.ready` is set by app.js once the core index is loaded
AND the map has gone idle once; `window.deep.renders` is bumped after every drawer render. Both
are read directly — never `networkidle` (a tiled map never reaches it) and never `map.on('load')`
(that is the style, not the data). No injected debug variable is needed: the app writes only the
URL fragment, so nothing it does can discard a query parameter, and the flag is unconditional.

A CHECK THAT CANNOT FAIL IS NOT A CHECK. Every check pairs a negative with a positive in the same
run (a search that must find something before one that must not), and `--prove-it-fails` points
the whole harness at a page with no app and requires every check to fail.

NO SOFTWARE-GL FLAGS BY DEFAULT. Bundled chromium on this machine reports a SwiftShader ANGLE
device with no flags (measured by the REWT and LCA harnesses, and re-measured here on 26 Sep 2026:
the map went idle and 23,448 points rendered with args=[]). `--gl` puts the trio back if a future
build needs it; the failure it produces should be the thing that teaches the flag.
"""
from __future__ import annotations

import argparse
import http.server
import re
import json
import os
import socketserver
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tools" / "pages" / "out"
GL = ["--enable-unsafe-swiftshader", "--use-gl=angle", "--use-angle=swiftshader"]


def serve(directory: Path, port: int):
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(directory), **k)

        def log_message(self, *a):
            pass

        def send_error(self, code, message=None, explain=None):
            # GitHub Pages serves 404.html (status 404) for a missing path; emulate that so the
            # identifier-tier check means the same thing locally as it does live.
            page = Path(directory) / "404.html"
            if code == 404 and page.exists():
                body = page.read_bytes()
                self.send_response(404)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
                return
            super().send_error(code, message, explain)

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", port), Quiet)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class Report:
    def __init__(self):
        self.rows = []

    def add(self, name, ok, detail=""):
        self.rows.append((name, ok, detail))
        print(f"  {'ok  ' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}", flush=True)

    @property
    def failed(self):
        return [r for r in self.rows if not r[1]]


def wait_ready(page, timeout=120_000) -> dict:
    """Returns the flags whether or not `ready` arrived, so a timeout is a result, not an exception."""
    try:
        page.wait_for_function("window.deep && window.deep.ready === true", timeout=timeout)
    except Exception:
        pass
    return page.evaluate("({ready: !!(window.deep && window.deep.ready), core: !!(window.deep && window.deep.coreLoaded), idle: !!(window.deep && window.deep.mapIdle), renders: window.deep ? window.deep.renders : null})")


def wait_render(page, before: int, timeout=60_000) -> bool:
    try:
        page.wait_for_function(f"window.deep && window.deep.renders > {before}", timeout=timeout)
        return True
    except Exception:
        return False


def goto_id(page, short_id: str, timeout=90_000) -> bool:
    """Open a record by its id via the URL fragment, and wait for the render. Setting location.hash
    to the value it already holds fires no hashchange, so the hash is cleared first; that cost two
    false failures before it was understood."""
    before = page.evaluate("window.deep.renders")
    page.evaluate(f"(() => {{ if (location.hash === '#id={short_id}') {{ location.hash = ''; }} setTimeout(() => {{ location.hash = '#id={short_id}'; }}, 30); }})()")
    return wait_render(page, before, timeout)


def wait_index(page, timeout=180_000) -> bool:
    try:
        page.wait_for_function("window.deep && window.deep.state && window.deep.state.indexReady === true", timeout=timeout)
        return True
    except Exception:
        return False


def check_boot(page, rep: Report, url: str):
    page.goto(url, wait_until="load", timeout=60_000)
    flags = wait_ready(page)
    rep.add("boot: window.deep.ready", flags["ready"], json.dumps(flags))
    if not flags["ready"]:
        # Say WHY, from the page's own words: the veil carries the boot error, and the 404 of a
        # missing data file reads as "Unexpected token '<'" there. A bare "not ready" once hid a
        # deploy with no data behind it for a whole verification pass.
        veil = page.evaluate("document.getElementById('veil-msg')?.innerText || ''")
        rep.add("boot: page's own message", False, veil[:160])
        return False
    # the first-visit modal must be open on a fresh profile, and closable
    rep.add("boot: info modal open on first visit", page.evaluate("!document.getElementById('info-modal').hidden"))
    page.evaluate("document.getElementById('info-close').click()")
    rep.add("boot: info modal closes", page.evaluate("document.getElementById('info-modal').hidden"))
    # the stats table was filled from the manifest (a number, not the empty template)
    stats = page.evaluate("document.getElementById('stats').innerText")
    rep.add("boot: stats filled from manifest", "539,372" in stats, stats.split("\n")[0][:60])
    # the map drew the point layer: query rendered features and require a positive count
    n = page.evaluate("window.deep.map.queryRenderedFeatures({layers:['places']}).length")
    rep.add("map: place points rendered", n > 100, f"{n} features in view")
    # attribution carries the licence obligation and the basemap credit
    attr = page.evaluate("document.querySelector('.maplibregl-ctrl-attrib-inner')?.textContent || ''")
    rep.add("map: attribution names EPNS/DEEP and CC BY-NC", "English Place-Name Society" in attr and "CC BY-NC" in attr, attr[:90])
    rep.add("map: attribution names the basemap provider", any(s in attr for s in ("CARTO", "OpenStreetMap", "National Library of Scotland")))
    # compact + collapsible, and not in the same corner as the scale bar
    layout = page.evaluate("({compact: !!document.querySelector('.maplibregl-ctrl-attrib.maplibregl-compact'), toggle: !!document.querySelector('.maplibregl-ctrl-attrib-button'), attribCorner: document.querySelector('.maplibregl-ctrl-attrib')?.closest('[class*=maplibregl-ctrl-bottom]')?.className, scaleCorner: document.querySelector('.maplibregl-ctrl-scale')?.closest('[class*=maplibregl-ctrl-bottom]')?.className})")
    rep.add("map: attribution is compact with a toggle", layout["compact"] and layout["toggle"], json.dumps(layout))
    rep.add("map: attribution and scale bar in different corners", layout["attribCorner"] != layout["scaleCorner"], f"{layout['attribCorner']} vs {layout['scaleCorner']}")
    # and the toggle works both ways
    page.evaluate("document.querySelector('.maplibregl-ctrl-attrib-button').click()")
    shown_after_1 = page.evaluate("document.querySelector('.maplibregl-ctrl-attrib').classList.contains('maplibregl-compact-show')")
    page.evaluate("document.querySelector('.maplibregl-ctrl-attrib-button').click()")
    shown_after_2 = page.evaluate("document.querySelector('.maplibregl-ctrl-attrib').classList.contains('maplibregl-compact-show')")
    rep.add("map: attribution toggle opens and closes", shown_after_1 != shown_after_2, f"{shown_after_1} -> {shown_after_2}")
    page.screenshot(path=str(OUT / "boot.png"))
    return True


def check_basemaps(page, rep: Report):
    ids = page.evaluate("[...document.querySelectorAll('input[name=basemap]')].map(i=>i.value)")
    rep.add("basemaps: three options offered", set(ids) >= {"carto", "osm", "nls"}, ",".join(ids))
    for bid, credit in (("nls", "National Library of Scotland"), ("osm", "OpenStreetMap contributors"), ("carto", "CARTO")):
        if bid not in ids:
            continue
        page.evaluate(f"document.querySelector('input[name=basemap][value={bid}]').click()")
        page.wait_for_timeout(600)
        attr = page.evaluate("document.querySelector('.maplibregl-ctrl-attrib-inner')?.textContent || ''")
        rep.add(f"basemaps: {bid} switches and attribution follows", credit in attr and page.evaluate("!!window.deep.map.getLayer('basemap')"), attr[:70])


def check_search(page, rep: Report):
    ok = wait_index(page)
    rep.add("search: name index loaded in the worker", ok)
    if not ok:
        return
    before = page.evaluate("window.deep.renders")
    page.fill("#q", "Bunsty")
    rep.add("search: 'Bunsty' renders results", wait_render(page, before), "")
    txt = page.evaluate("document.getElementById('drawer').innerText")
    rep.add("search: 'Bunsty' finds Bunsty Hundred", "Bunsty Hundred" in txt, txt[:80].replace("\n", " | "))
    # a historical spelling reaches its place through the variant index
    before = page.evaluate("window.deep.renders")
    page.fill("#q", "Bonestou")
    wait_render(page, before)
    txt = page.evaluate("document.getElementById('drawer').innerText")
    rep.add("search: spelling 'Bonestou' -> Bunsty Hundred", "Bunsty" in txt and "spelling of" in txt, txt[:80].replace("\n", " | "))
    # the negative, paired with the positives above in the same run
    before = page.evaluate("window.deep.renders")
    page.fill("#q", "qzxqzxqzx")
    wait_render(page, before)
    txt = page.evaluate("document.getElementById('drawer').innerText")
    rep.add("search: nonsense finds nothing", "No spelling matches" in txt, txt[:60])
    page.screenshot(path=str(OUT / "search.png"))


def check_place(page, rep: Report):
    # Establish the state this check needs rather than inherit it from `search` having run first:
    # run alone (--check place) it once failed on an unloaded index and blamed the ranking.
    if not wait_index(page):
        rep.add("place: name index loaded in the worker", False)
        return
    before = page.evaluate("window.deep.renders")
    page.fill("#q", "Bunsty")
    wait_render(page, before)
    # the hundred should be the FIRST result (a hundred outranks a farm at equal match quality)
    first = page.evaluate("document.querySelector('#drawer li[data-gid] .form')?.innerText || ''")
    rep.add("search: the hundred outranks the farm", first == "Bunsty Hundred", first)
    before = page.evaluate("window.deep.renders")
    page.evaluate("[...document.querySelectorAll('#drawer li[data-gid]')].find(li => li.innerText.includes('Bunsty Hundred')).click()")
    rep.add("place: opening a result renders the place page", wait_render(page, before, 90_000))
    txt = page.evaluate("document.getElementById('drawer').innerText")
    rep.add("place: headword and DEEP id shown", "Bunsty Hundred" in txt and "epns-deep-02-hu-subcounty-000001" in txt)
    # by date: "1086  Bonestou  DB" on one row; as printed: "Bonestou 1086 DB"
    row = page.evaluate("[...document.querySelectorAll('#drawer .forms li')].map(li => li.innerText.replace(/\\s+/g,' ')).find(t => t.includes('1086') && t.includes('DB')) || ''")
    rep.add("place: the Domesday attestation is rendered (1086 · Bonestou · DB)", "Bonestou" in row and "1086" in row and "DB" in row, row[:60])
    rep.add("place: breadcrumb reaches the county", "Buckinghamshire" in txt)
    rep.add("place: children listed (parishes of the hundred)", "Cold Brayfield" in txt)
    rep.add("place: URL fragment carries the id", page.evaluate("location.hash") == "#id=02-hu-subcounty-000001", page.evaluate("location.hash"))
    # chronological view (default): rows ordered by begin year, undated last, and complete
    order = page.evaluate("[...document.querySelectorAll('#drawer .forms.chron li')].map(li => li.dataset.b)")
    years = [int(b) for b in order if b != ""]
    dated_then_undated = order == [b for b in order if b != ""] + [b for b in order if b == ""]
    rep.add("forms: chronological rows are in non-decreasing year order", len(years) > 5 and years == sorted(years) and dated_then_undated, f"{len(order)} rows, {years[:4]}…{years[-2:]}")
    n_chron = page.evaluate("document.querySelectorAll('#forms-wrap .att').length")
    n_expect = page.evaluate("+document.getElementById('forms-wrap').dataset.n")
    page.evaluate("document.querySelector('.view-tog button[data-view=printed]').click()")
    n_printed = page.evaluate("document.querySelectorAll('#forms-wrap .att').length")
    rep.add("forms: both views hold every attestation", n_chron == n_printed == n_expect and n_expect > 0, f"chron {n_chron}, printed {n_printed}, record {n_expect}")
    rep.add("forms: toggle reflected in the URL", "view=printed" in page.evaluate("location.hash"))
    page.evaluate("document.querySelector('.view-tog button[data-view=date]').click()")
    page.screenshot(path=str(OUT / "forms-chron.png"))
    # tooltips: hover the first attestation; the panel must appear and gloss the source (DB = Domesday Book)
    forms = page.evaluate("document.querySelector('#drawer .forms')?.innerText || ''")
    rep.add("place: no empty attestation in the forms (', ,')", forms != "" and ", ," not in forms, forms[:60].replace("\n", " | "))
    page.hover("#drawer .att")
    page.wait_for_timeout(300)
    tipvis = page.evaluate("!document.getElementById('att-tip').hidden")
    tiptxt = page.evaluate("document.getElementById('att-tip').innerText")
    rep.add("tooltip: appears on hover and glosses Domesday Book", tipvis and "Domesday Book" in tiptxt and "1086" in tiptxt, tiptxt[:90].replace("\n", " | "))
    page.screenshot(path=str(OUT / "tooltip.png"))
    page.mouse.move(700, 450)
    page.wait_for_timeout(200)
    rep.add("tooltip: hides when the pointer leaves", page.evaluate("document.getElementById('att-tip').hidden"))
    # the Portland line from the report: a passim run with a copy-date; no ', ,' and a copy-date gloss.
    # First in the chronological view: the run is ONE row at its first date, running on to the last.
    goto_id(page, "52-b-subparish-000031")
    row = page.evaluate("[...document.querySelectorAll('#drawer .forms.chron li')].map(li => li.innerText.replace(/\\s+/g,' ')).find(t => t.includes('Portlond(e)') && t.includes('1268')) || ''")
    rep.add("forms: a passim run is one chronological row", "1268" in row and "1460" in row and re.search(r"et (passim|freq)", row) is not None, row[:90])
    # then the printed view, which the toggle must persist into (set here, read on the next place)
    page.evaluate("document.querySelector('.view-tog button[data-view=printed]').click()")
    rep.add("forms: 'as printed' selected persists as the pressed state", page.evaluate("document.querySelector('.view-tog button[data-view=printed]').getAttribute('aria-pressed') === 'true'"))
    line = page.evaluate("[...document.querySelectorAll('#drawer .forms li')].map(li => li.innerText).find(t => t.startsWith('Portlond(e)')) || ''")
    rep.add("place: Portland's passim run renders without an empty item", line.startswith("Portlond(e)") and ", ," not in line
            and re.search(r"\bet (passim|freq)\b[^,]*\d{4}", line) is not None, line[:90])
    page.hover("#drawer .forms li[data-v='w579358'] .att")
    page.wait_for_timeout(300)
    tiptxt = page.evaluate("document.getElementById('att-tip').innerText")
    rep.add("tooltip: copy-date (l13) glossed as late 13th century", "late 13th century" in tiptxt and "1275" in tiptxt, tiptxt[:120].replace("\n", " | "))
    page.screenshot(path=str(OUT / "tooltip-portland.png"))
    page.mouse.move(700, 450)
    # persistence across places: reopen Bunsty Hundred and expect the printed view still selected
    page.evaluate("location.hash = ''")
    page.wait_for_timeout(100)
    before = page.evaluate("window.deep.renders")
    page.evaluate("location.hash = '#id=02-hu-subcounty-000001&view=printed'")
    wait_render(page, before, 90_000)
    rep.add("forms: view choice persists across places", page.evaluate("!!document.querySelector('#drawer .forms:not(.chron)') && document.querySelector('.view-tog button[data-view=printed]').getAttribute('aria-pressed') === 'true'"))
    page.evaluate("document.querySelector('.view-tog button[data-view=date]').click()")
    # a field-name, which lives only in the county file, opens via its id
    rep.add("place: field-name opens from the county file", goto_id(page, "28-e-fn-000001"))
    txt = page.evaluate("document.getElementById('drawer').innerText")
    rep.add("place: field-name shows its township", "Alder Wood" in txt and "Alfreton" in txt, txt[:80].replace("\n", " | "))
    page.screenshot(path=str(OUT / "place.png"))


def check_formats(page, rep: Report):
    """The three per-record views, and the browser port's equality with the Python export."""
    if not wait_index(page):
        rep.add("formats: name index loaded", False)
        return
    goto_id(page, "02-hu-subcounty-000001")
    # PLATO view
    before = page.evaluate("window.deep.renders")
    page.evaluate("document.querySelector('.formats button[data-fmt=plato]').click()")
    wait_render(page, before, 30_000)
    body = page.evaluate("document.getElementById('fmt-body').innerText")
    cav = page.evaluate("document.getElementById('fmt-caveat').innerText")
    expect = json.loads((ROOT / "docs" / "data" / "plato-sample.json").read_text())
    want = expect["plato_commit"][:12]
    rep.add("formats: PLATO view opens with Headword formStatus and the commit the fixture was built at", "https://w3id.org/plato#Headword" in body and "commit" in cav and want in cav
            and (not expect.get("plato_tag") or expect["plato_tag"] in cav), cav[:100])
    page.evaluate("document.getElementById('fmt-close').click()")
    # LPF view with losses struck in place
    before = page.evaluate("window.deep.renders")
    page.evaluate("document.querySelector('.formats button[data-fmt=lpf]').click()")
    wait_render(page, before, 30_000)
    n_drop = page.evaluate("document.querySelectorAll('#fmt-body .drop').length")
    body = page.evaluate("document.getElementById('fmt-body').innerText")
    rep.add("formats: LPF view is a Feature with losses marked in place", '"type": "Feature"' in body and n_drop > 0 and "discussion 53" in page.evaluate("document.getElementById('fmt-caveat').innerText"), f"{n_drop} losses marked")
    page.screenshot(path=str(OUT / "lpf-view.png"))
    page.evaluate("document.getElementById('fmt-close').click()")
    # MADS view
    before = page.evaluate("window.deep.renders")
    page.evaluate("document.querySelector('.formats button[data-fmt=mads]').click()")
    wait_render(page, before, 30_000)
    body = page.evaluate("document.getElementById('fmt-body').innerText")
    rep.add("formats: MADS view regenerates the record", '<mads ID="epns-deep-02-hu-subcounty-000001">' in body and '<attestation variantID=' in body and "<geographic" in body)
    page.evaluate("document.getElementById('fmt-close').click()")
    # RDF view: N-Triples built in the page (jsonld.js, loaded on first use), stamped with the pinned context commit
    rules = json.loads((ROOT / "docs" / "data" / "rdf" / "rules.json").read_text())
    before = page.evaluate("window.deep.renders")
    page.evaluate("document.querySelector('.formats button[data-fmt=rdf]').click()")
    wait_render(page, before, 90_000)
    body = page.evaluate("document.getElementById('fmt-body').innerText")
    cav = page.evaluate("document.getElementById('fmt-caveat').innerText")
    rep.add("formats: RDF view gives N-Triples typing the record as a PLATO SpatialEntity, at the pinned context commit",
            "<https://w3id.org/whg-epns/02/000002> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <https://w3id.org/plato#SpatialEntity> ." in body
            and rules["context_commit"][:12] in cav and "triples" in cav, cav[:100] if "triples" in cav else body[:160])
    page.evaluate("document.getElementById('fmt-close').click()")
    # parity: for every sample record, the browser port must equal the Python export exactly
    res = page.evaluate("""async () => {
      const sample = await (await fetch('data/plato-sample.json', {cache:'no-cache'})).json();
      const out = {commit: sample.plato_commit, ok: [], bad: []};
      for (const [sid, ref] of Object.entries(sample.records)) {
        const okOpen = await (async () => { const b = window.deep.renders; if (location.hash === '#id=' + sid) { location.hash = ''; await new Promise(r => setTimeout(r, 40)); } location.hash = '#id=' + sid; for (let i = 0; i < 400 && window.deep.renders === b; i++) await new Promise(r => setTimeout(r, 50)); return window.deep.renders > b; })();
        if (!okOpen) { out.bad.push(sid + ': did not open'); continue; }
        const rec = window.deep.state.currentRec, ctx = window.deep.formats.ctx();
        const p = window.deep.formats.toPlato(rec, ctx), l = window.deep.formats.toLpf(rec, ctx);
        const same = JSON.stringify(p.entity) === JSON.stringify(ref.plato) && JSON.stringify(p.identityRelations) === JSON.stringify(ref.identityRelations) && JSON.stringify(l.feature) === JSON.stringify(ref.lpf);
        (same ? out.ok : out.bad).push(sid);
      }
      return out;
    }""")
    rep.add(f"formats: browser port equals the Python export on all {len(res['ok']) + len(res['bad'])} sample records", len(res["bad"]) == 0 and len(res["ok"]) >= 5, ", ".join(res["bad"])[:120] or f"commit {res['commit'][:12]}")
    # the comparator's own control: a record compared with a mutated copy of itself must differ
    ctl = page.evaluate("""() => { const rec = window.deep.state.currentRec, ctx = window.deep.formats.ctx(); const a = window.deep.formats.toPlato(rec, ctx).entity; const b = JSON.parse(JSON.stringify(a)); b.attestations[0].notes += 'x'; return JSON.stringify(a) !== JSON.stringify(b); }""")
    rep.add("formats: the parity comparator can fail (mutated copy differs)", ctl)
    # RDF parity: the browser's triples for each sample record, canonicalised, must equal the Python's
    res = page.evaluate("""async () => {
      const [sample, want] = await Promise.all(['data/plato-sample.json', 'data/rdf/sample.json'].map(u => fetch(u, {cache:'no-cache'}).then(r => r.json())));
      const out = {ok: [], bad: [], control: null};
      const got = {};
      for (const [sid, ref] of Object.entries(sample.records)) {
        const r = await window.deep.formats.toRdf(ref.plato, ref.identityRelations);
        got[sid] = await window.deep.formats.rdfCanonical(r.text);
        (want.records[sid] && got[sid] === want.records[sid].canonical ? out.ok : out.bad).push(sid);
      }
      const ids = Object.keys(got);
      out.control = got[ids[0]] !== want.records[ids[1]].canonical;   // a different record's graph must not match
      return out;
    }""")
    rep.add(f"formats: browser RDF equals the Python triplifier's graph on all {len(res['ok']) + len(res['bad'])} sample records (canonical N-Quads)",
            len(res["bad"]) == 0 and len(res["ok"]) >= 5 and res["control"], ", ".join(res["bad"])[:120] or f"{len(res['ok'])} records; control differs: {res['control']}")


def check_identifiers(page, rep: Report, url: str):
    """The persistent-identifier tier: #u= routing by DEEP's county-wide serial, the per-record static
    files a w3id would negotiate to, and the 404 page that carries a person to a record without one."""
    if not wait_index(page):
        rep.add("ids: name index loaded", False)
        return
    # #u=02/000002 is Bunsty Hundred (DEEP URI .../placename/02/000002); a field-name only in a county file
    before = page.evaluate("window.deep.renders")
    page.evaluate("location.hash = ''; setTimeout(() => { location.hash = '#u=02/000002'; }, 30)")
    ok = wait_render(page, before, 90_000)
    txt = page.evaluate("document.getElementById('drawer').innerText")
    rep.add("ids: #u=02/000002 opens Bunsty Hundred", ok and "Bunsty Hundred" in txt and "epns-deep-02-hu-subcounty-000001" in txt)
    before = page.evaluate("window.deep.renders")
    page.evaluate("location.hash = '#u=28/000105'")
    ok = wait_render(page, before, 90_000)
    txt = page.evaluate("document.getElementById('drawer').innerText")
    rep.add("ids: #u= reaches a field-name through its county file", ok and "Alder Wood" in txt, txt[:60].replace("\n", " | "))
    before = page.evaluate("window.deep.renders")
    page.evaluate("location.hash = '#u=02/999999'")
    wait_render(page, before, 30_000)
    rep.add("ids: an unknown serial says so", "No record numbered" in page.evaluate("document.getElementById('drawer').innerText"))
    # a record whose one cited spelling is its own heading (DEEP gave heading and record one URI): the page
    # showed no spellings at all before the heading joined the record's name list
    before = page.evaluate("window.deep.renders")
    page.evaluate("location.hash = '#u=06/001111'")
    wait_render(page, before, 30_000)
    li = page.evaluate("[...document.querySelectorAll('#drawer ul.forms li')].map(x => x.innerText).join(' | ')")
    rep.add("ids: a citation of the heading itself is shown (Norton, 06/001111)", "Norton" in li, li[:100])
    # static files for a parish-level record
    base = url if url.endswith("/") else url + "/"
    import urllib.request
    got = {}
    def probe(ext, body):
        if ext == "json":
            d = json.loads(body)
            return d.get("profile") == "place-centric" and len(d.get("spatialEntities", [])) == 1 and d["spatialEntities"][0]["label"] == "Bunsty Hundred"
        if ext == "geojson":
            d = json.loads(body)
            return d.get("type") == "FeatureCollection" and len(d.get("features", [])) == 1 and d["features"][0]["properties"]["title"] == "Bunsty Hundred"
        return body.startswith("<?xml") and 'ID="epns-deep-02-hu-subcounty-000001"' in body and "<attestation" in body
    for ext in ("json", "geojson", "xml"):
        try:
            with urllib.request.urlopen(base + f"id/02/000002.{ext}", timeout=30) as resp:
                body = resp.read().decode("utf-8")
                got[ext] = (resp.status, probe(ext, body), "plato_commit=" in body or ext == "xml")
        except Exception as e:  # noqa: BLE001
            got[ext] = (getattr(e, "code", str(e)), False, False)
    rep.add("ids: static PLATO / LPF / MADS files exist for a parish-level record", all(v[0] == 200 and v[1] and v[2] for v in got.values()), json.dumps(got))
    # source IRIs: every citation in the record names its source by IRI, and each IRI has its own file
    W = "https://w3id.org/whg-epns/"
    try:
        with urllib.request.urlopen(base + "id/02/000002.json", timeout=30) as resp:
            rec = json.loads(resp.read())
        srcs = [c["source"] for a in rec["spatialEntities"][0].get("attestations", []) for c in a.get("citations", []) if "source" in c]
        srcs += [s["derivedFrom"] for s in srcs if "derivedFrom" in s]
        unnamed = [s for s in srcs if not str(s.get("@id", "")).startswith(W)]
        rep.add("ids: every cited source in a record has a whg-epns IRI", bool(srcs) and not unnamed, f"{len(srcs)} sources, {len(unnamed)} without an IRI")
        want = sorted({s["@id"] for s in srcs})[:3] + [W + "source/DB", W + "volume/52", W + "source/ASC/witness/B-c-1000", W + "source/deep"]
        res = {}
        # dcterms:license under either spelling: since PLATO 0.9 the Source context has the term "licence" for it
        has_lic = lambda x: "dcterms:license" in x or "licence" in x  # noqa: E731
        for iri in want:
            try:
                with urllib.request.urlopen(base + "id/" + iri[len(W):] + ".json", timeout=30) as resp:
                    d = json.loads(resp.read())
                    nodes = {x.get("@id"): x for x in d.get("@graph", [])}
                    doc = next((x for x in nodes.values() if x.get("foaf:primaryTopic", {}).get("@id") == iri), {})
                    # the source node names itself; the licence sits on the document node, never on the source
                    res[iri[len(W):]] = resp.status == 200 and iri in nodes and "@context" in d and has_lic(doc) and not has_lic(nodes[iri])
            except Exception as e:  # noqa: BLE001
                res[iri[len(W):]] = getattr(e, "code", str(e))
        rep.add("ids: source IRIs dereference to JSON-LD naming themselves", all(v is True for v in res.values()), json.dumps(res))
        with urllib.request.urlopen(base + "id/02/000008.json", timeout=30) as resp:     # Cold Brayfield has GeoNames matches
            asserted = {r.get("assertedBy") for r in json.loads(resp.read()).get("identityRelations", [])}
        with urllib.request.urlopen(base + "id/agent/deep.json", timeout=30) as resp:
            ag = {x.get("@id"): x for x in json.loads(resp.read()).get("@graph", [])}
        rep.add("ids: identity relations are asserted by the DEEP agent, which dereferences",
                asserted == {W + "agent/deep"} and "plato:Contributor" in ag.get(W + "agent/deep", {}).get("@type", []), json.dumps(sorted(asserted)))
    except Exception as e:  # noqa: BLE001
        rep.add("ids: every cited source in a record has a whg-epns IRI", False, str(e)[:120])
    # a record below parish level has no static machine file: 404 (an honest one), and the 404 page routes people
    try:
        urllib.request.urlopen(base + "id/28/000105.json", timeout=30)
        rep.add("ids: no static machine file below parish level (404 expected)", False, "200")
    except Exception as e:  # noqa: BLE001
        rep.add("ids: no static machine file below parish level (404 expected)", getattr(e, "code", None) == 404, str(getattr(e, "code", e)))
    page.goto(base + "id/28/000105.json", wait_until="load", timeout=60_000)
    # the 404 page redirects to #u=28/000105; the app opens the record and rewrites the hash to its id
    try:
        page.wait_for_function("location.hash === '#id=28-e-fn-000001' && document.getElementById('drawer') && document.getElementById('drawer').innerText.includes('Alder Wood')", timeout=90_000)
        rep.add("ids: the 404 page carries a person to the record", True)
    except Exception:
        rep.add("ids: the 404 page carries a person to the record", False, page.evaluate("location.href")[-50:])
    page.goto(url, wait_until="load", timeout=60_000)
    wait_ready(page)


def check_browse(page, rep: Report):
    sel = page.query_selector("#nav-county")
    n = page.evaluate("document.getElementById('nav-county').options.length - 1")
    rep.add("browse: county list populated (one option per volume)", n == 66, f"{n} options")
    labels = page.evaluate("[...document.getElementById('nav-county').options].slice(1).map(o => (o.parentElement.tagName === 'OPTGROUP' ? o.parentElement.label + ' / ' : '') + o.textContent)")
    dupes = sorted({l for l in labels if labels.count(l) > 1})
    rep.add("browse: no two options read the same", not dupes, ", ".join(dupes)[:100])
    ches = page.evaluate("[...document.querySelectorAll('#nav-county optgroup')].find(g => g.label.startsWith('Cheshire'))?.querySelectorAll('option').length")
    rep.add("browse: multi-volume counties are grouped and labelled by volume", ches == 4 and any("vol. 44: Macclesfield" in l for l in labels), f"Cheshire group has {ches} options")
    before = page.evaluate("window.deep.renders")
    gid = page.evaluate("[...document.getElementById('nav-county').options].find(o=>o.text==='Buckinghamshire').value")
    page.select_option("#nav-county", gid)
    rep.add("browse: county page renders", wait_render(page, before, 90_000))
    txt = page.evaluate("document.getElementById('drawer').innerText")
    rep.add("browse: county page lists its hundreds", "Bunsty Hundred" in txt and "hundred" in txt.lower())


# A word the code printed because a value was missing, never one the survey wrote.
PLACEHOLDER = re.compile(r"\b(undefined|null|NaN)\b|\[object ")


def check_placeholders(page, rep: Report):
    """A missing part is left out, never printed as "undefined", "null" or "NaN". Found 16 Nov 2026:
    the top hit for 'Bunsty' read "undefined Bunsty Hundred", because the name index's derived keys
    (kind 3 in export_site.py: 45,666 rows) had no entry in the result row's table of kind words.
    Every row here asserts a presence first, so an empty drawer cannot pass as a clean one."""
    if not wait_index(page):
        rep.add("placeholders: name index loaded in the worker", False)
        return

    def results(q):
        before = page.evaluate("window.deep.renders")
        page.fill("#q", "")
        page.fill("#q", q)
        wait_render(page, before)
        return page.evaluate("[...document.querySelectorAll('#drawer li[data-gid]')].map(l => [l.querySelector('.form')?.innerText || '', l.querySelector('.ctx')?.innerText || ''])")

    def clean(name, text, need):
        bad = PLACEHOLDER.search(text or "")
        rep.add(name, need in (text or "") and not bad, (f"prints '{bad.group(0)}': " if bad else "") + (text or "")[:110].replace("\n", " | "))

    # search results. Bunsty and Seckloe reach their hundreds through a key derived from the headword,
    # Newport reaches 'Neuport, Newport' through one derived from a spelling.
    for q, form, ctx in (("Bunsty", "Bunsty Hundred", "hundred / wapentake"), ("Seckloe", "Seckloe Hundred", "hundred / wapentake"),
                         ("Newport", "Neuport, Newport", "spelling of Newport")):
        rows = results(q)
        hit = next((c for f, c in rows if f == form), None)
        rep.add(f"placeholders: '{q}' finds {form}, read as: {ctx}…", hit is not None and hit.startswith(ctx), str(hit)[:90])
        clean(f"placeholders: no result for '{q}' prints one", " || ".join(f + " " + c for f, c in rows), form)
    # a hundred's parent IS its county (409 places are so): the county is said once, not "Buckinghamshire · Buckinghamshire"
    hit = next((c for f, c in results("Bunsty") if f == "Bunsty Hundred"), None)
    rep.add("placeholders: a place directly under its county names the county once", hit == "hundred / wapentake · Buckinghamshire", str(hit)[:90])
    # record panels: a hundred, a place with a 'late 12' century date, one with empty search forms,
    # one with an unparseable coordinate, a field-name, and the one record that has no headword at all
    for sid, need in (("02-hu-subcounty-000001", "Bunsty Hundred"), ("05-b-subparish-000101", "Farndale"), ("04-b-subparish-000067", "Hallow"),
                      ("05-a-parish-000145", "unparseable"), ("28-e-fn-000001", "Alfreton"), ("76-e-fn-003800", "epns-deep-76-e-fn-003800")):
        goto_id(page, sid)
        clean(f"placeholders: record {sid} prints none", page.evaluate("document.getElementById('drawer').innerText"), need)
    # the tooltip of a century date written 'late 12' (40 such, of 'l12', 'e 13', 'early 13' kinds) read "NaNth century"
    goto_id(page, "05-b-subparish-000101")
    page.evaluate("document.querySelector('.view-tog button[data-view=printed]')?.click()")
    att = page.query_selector("#drawer .forms li[data-v='w83252'] .att")
    if att:
        att.hover()
        page.wait_for_timeout(300)
    clean("placeholders: tooltip glosses 'late 12' as the late 12th century", page.evaluate("document.getElementById('att-tip')?.innerText || ''"), "late 12th century")
    page.mouse.move(700, 450)
    page.evaluate("document.querySelector('.view-tog button[data-view=date]')?.click()")
    # search forms the data leaves empty (14 of them) are left out, and the count beside them agrees
    goto_id(page, "04-b-subparish-000067")
    st = page.evaluate("(() => { const d = [...document.querySelectorAll('#drawer details')].find(x => x.querySelector('summary')?.textContent.startsWith('Normalised search forms')); return d ? [d.querySelector('summary').textContent, d.querySelector('p').textContent] : null; })()")
    items = st[1].split(", ") if st else []
    rep.add("placeholders: Hallow's search forms have no empty item, and the count is theirs", bool(st) and "heallinga weallan" in items and all(i.strip() for i in items) and f"({len(items)})" in st[0], f"{st[0] if st else st}; {len(items)} items")
    # the browse list and a county page
    labels = page.evaluate("[...document.getElementById('nav-county').options].slice(1).map(o => o.textContent)")
    clean("placeholders: the browse list prints none", " | ".join(labels), "vol. 44")
    gid = page.evaluate("[...document.getElementById('nav-county').options].find(o => o.text === 'Buckinghamshire')?.value")
    before = page.evaluate("window.deep.renders")
    page.select_option("#nav-county", gid)
    wait_render(page, before, 90_000)
    clean("placeholders: a county page prints none", page.evaluate("document.getElementById('drawer').innerText"), "Bunsty Hundred")
    # the map's hover label, on whichever place points are drawn
    tips = page.evaluate("""async () => { const m = window.deep.map, out = []; const fs = m.queryRenderedFeatures({layers: ['places']}).slice(0, 400);
        for (let k = 0; k < fs.length && out.length < 12; k += 33) { const pt = m.project(fs[k].geometry.coordinates);
          m.fire('mousemove', {point: pt, lngLat: m.unproject(pt), features: [fs[k]], originalEvent: {}, type: 'mousemove'});
          const el = document.querySelector('.maplibregl-popup.tip .maplibregl-popup-content'); if (el) out.push(el.innerText.replace(/\\n/g, ' ')); }
        m.fire('mouseleave', {type: 'mouseleave'}); return out; }""")
    clean(f"placeholders: map hover labels print none ({len(tips)} hovered)", " | ".join(tips), " · ")


def check_phonetic(page, rep: Report):
    """Opt-in phonetic matching: loads the model (CDN runtime) and the matrix, then a medieval spelling
    with no letters in common must find its modern headword. Skipped with a FAIL, not silently, if
    the matrix has not been built."""
    page.evaluate("document.getElementById('phon').click()")
    try:
        page.wait_for_function("window.deep.state.phonReady === true", timeout=240_000)
    except Exception:
        rep.add("phonetic: model and matrix load", False, page.evaluate("document.getElementById('status').innerText")[:120])
        return
    rep.add("phonetic: model and matrix load", True)
    def phon(q):
        before = page.evaluate("window.deep.renders")
        page.fill("#q", q)
        wait_render(page, before)
        try:
            page.wait_for_function("(() => { const t = document.getElementById('drawer').innerText.toLowerCase(); return t.includes('sounds like') || t.includes('nothing sounds'); })()", timeout=60_000)
        except Exception:
            pass
        # the phonetic section lists places NOT already shown by the spelling search, so a target may
        # legitimately sit in either; return both, in order
        return page.evaluate("""() => { const d = document.getElementById('drawer'); const out = {phon: [], text: []};
            for (const sec of d.querySelectorAll('.rs-sec')) { const ul = sec.nextElementSibling; if (!ul) continue;
              const rows = [...ul.querySelectorAll('li')].map(li => li.innerText.replace(/\\s+/g, ' '));
              (sec.innerText.toLowerCase().includes('sounds like') ? out.phon : out.text).push(...rows); }
            return out; }""")
    # medieval spellings and a bare word must surface the right PLACE: in the spelling results, or in
    # the top three of the phonetic additions; and the specific junk measured offline must be gone
    # Snatchill Lodge is NOT junk for Snotingaham: its DEEP spelling "Snotengaham" is one letter off
    # the query (Dice 0.8), so it survives the veto on merit; the junk named here was measured offline.
    for q, target, junk in (("Snotingaham", "Nottingham", None), ("Grantebrige", "Cambridge", None), ("Bunsty", "Bunsty Hundred", "Bumpstead"), ("Bragenfeld", "Cold Brayfield", None), ("york", "York", "Hullampton")):
        r = phon(q)
        found = any(target in c for c in r["text"]) or any(target in c for c in r["phon"][:3])
        rep.add(f"phonetic: '{q}' surfaces {target}", found, (" || ".join(r["phon"][:3]) or "no phonetic additions")[:110])
        if junk:
            rep.add(f"phonetic: '{q}' no longer offers {junk}", not any(junk in c for c in r["phon"][:10]))
    txt = page.evaluate("document.getElementById('drawer').innerText")
    # The floor is applied: every score shown is within [0.70, 1.00] and the list is bounded. A
    # "nonsense finds nothing" control is NOT used here: to a phonetic model a short random string
    # is not noise (xqzpv scored 0.94 against Sceb when tried), so that assertion would test the
    # model's opinion of gibberish rather than the page's handling of scores.
    scores = page.evaluate("[...document.querySelectorAll('#drawer .rs .score')].map(e => +e.innerText)")
    rep.add("phonetic: scores respect the 0.70 cosine floor and the cap, one line per place", len(scores) > 0 and len(scores) <= 25 and all(0.70 <= x <= 1.01 for x in scores),
            f"{len(scores)} scores, min {min(scores) if scores else None}")
    page.evaluate("document.getElementById('phon').click()")
    page.screenshot(path=str(OUT / "phonetic.png"))


def check_downloads(page, rep: Report, url: str):
    """The downloads page: present, quantitative about LPF's null geometry, and pointing at real files."""
    r = page.goto(url.rstrip("/") + "/downloads.html", wait_until="load", timeout=60_000)
    rep.add("downloads: page served", r is not None and r.status == 200, str(r.status if r else None))
    txt = page.evaluate("document.body.innerText")
    rep.add("downloads: LPF caveat is quantitative (null-geometry share) and links discussion 53",
            "null" in txt and "%" in txt and "23,448" in txt and "discussion 53" in txt and "PLATO" in txt, "")
    rep.add("downloads: licence statement present verbatim", "licensed to Jisc by the English Place Names Society" in txt)
    hrefs = page.evaluate("[...document.querySelectorAll('main a[href*=\"releases/download\"]')].map(a => a.href)")
    rep.add("downloads: release asset links listed", len(hrefs) >= 5, f"{len(hrefs)} links")
    rep.add("downloads: RDF triples offered", any(h.endswith("/deep-plato.nt.gz") for h in hrefs), f"{len(hrefs)} links")
    # Every asset link must resolve. Checked from Python, not the page: a cross-origin HEAD from the
    # browser is blocked by CORS and would report a failure that says nothing about the file.
    import urllib.request
    missing = []
    for h in hrefs:
        try:
            req = urllib.request.Request(h, method="HEAD")
            with urllib.request.urlopen(req, timeout=30) as resp:   # follows GitHub's 302 to the CDN
                if resp.status != 200:
                    missing.append(f"{h.split('/')[-1]}:{resp.status}")
        except Exception as e:  # noqa: BLE001
            missing.append(f"{h.split('/')[-1]}:{getattr(e, 'code', e)}")
    # While the repository is private the assets 404 to anonymous visitors and the page SAYS so
    # (the access note); the check then requires the note and the 404s to agree. Once public, the
    # note disappears and every link must resolve.
    note = "the repository that holds these release files is private" in txt
    if note:
        all_404 = len(missing) == len(hrefs) and all(m.endswith(":404") for m in missing)
        rep.add("downloads: access note present and consistent with anonymous 404s", len(hrefs) > 0 and all_404, f"{len(missing)}/{len(hrefs)} 404")
    else:
        rep.add("downloads: every release asset link resolves", len(hrefs) > 0 and not missing, ", ".join(missing)[:120] or f"{len(hrefs)} assets")
    page.goto(url, wait_until="load", timeout=60_000)
    wait_ready(page)


def check_cache(page, rep: Report, url: str):
    """Second load must come from IndexedDB: same readiness, no core.json request over the wire."""
    fetched = []
    page.on("request", lambda r: fetched.append(r.url) if "data/core.json" in r.url else None)
    page.goto(url, wait_until="load", timeout=60_000)
    flags = wait_ready(page)
    rep.add("cache: second load ready", flags["ready"])
    rep.add("cache: core.json served from IndexedDB (no network request)", len(fetched) == 0, f"{len(fetched)} request(s)")


def check_offline(pw, rep: Report, url: str, httpd=None):
    """The page must START, and the record, search, sound-alike and RDF views must work, with no network at all.

    It uses its OWN browser profile in a temporary directory (never the user's), in two phases that share
    that profile, so the service worker and IndexedDB survive between them:
      1. online: load the page, wait for the service worker, press "save everything for offline use";
      2. the browser is closed and started again with the network GONE, and the page is loaded in it.
    "Gone" means: for a deployed URL, every hostname fails to resolve (--host-resolver-rules), which
    reaches the service worker's own fetches too (Playwright's context.set_offline does NOT: measured,
    a service worker still reached the server); for --serve, the loopback server is stopped.
    Controls, because "works offline" passes whenever the network quietly stayed on:
      - a cross-origin fetch must be blocked;
      - a same-origin fetch of a file that exists nowhere must fail at the NETWORK (a 404 would mean the
        server answered, so the browser was never offline).
    Basemap tiles are NOT expected to load offline; the check asserts the place points still draw."""
    import shutil
    import tempfile
    prof = tempfile.mkdtemp(prefix="deep-offline-")
    try:
        ctx = pw.chromium.launch_persistent_context(prof, viewport={"width": 1400, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url, wait_until="load", timeout=60_000)
        wait_ready(page)
        sw = page.evaluate("""async () => { if (!('serviceWorker' in navigator)) return 'unsupported';
            const t = new Promise(r => setTimeout(() => r('none'), 8000));
            return await Promise.race([navigator.serviceWorker.ready.then(() => 'ready'), t]); }""")
        rep.add("offline: service worker state (information)", True, sw)
        if page.query_selector("#prep-offline"):
            page.evaluate("document.getElementById('info-modal').hidden = false")
            page.evaluate("document.getElementById('prep-offline').click()")
            try:
                page.wait_for_function("document.getElementById('prep-msg').innerText.startsWith('done') || document.getElementById('prep-msg').innerText.startsWith('stopped')", timeout=420_000)
            except Exception:
                pass
            msg = page.evaluate("document.getElementById('prep-msg').innerText")
            rep.add("offline: 'save everything for offline use' completes", msg.startswith("done"), msg[:100])
        else:
            rep.add("offline: page offers 'save everything for offline use'", False, "no #prep-offline button")
        page.wait_for_timeout(1500)   # let IndexedDB writes finish before the browser is closed
        ctx.close()
        # phase 2: the network is gone
        args = ["--host-resolver-rules=MAP * ~NOTFOUND"]
        if httpd is not None:   # loopback is the one host the local server needs; stopping it is what cuts that
            httpd.shutdown()
            httpd.server_close()
            args = ["--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"]
        ctx = pw.chromium.launch_persistent_context(prof, args=args, viewport={"width": 1400, "height": 900})
        try:
            p2 = ctx.pages[0] if ctx.pages else ctx.new_page()
            errs = []
            p2.on("pageerror", lambda e: errs.append(str(e)[:120]))
            try:
                p2.goto(url, wait_until="load", timeout=30_000)
            except Exception as e:  # noqa: BLE001
                rep.add("offline: the page document loads", False, str(e)[:100])
                return
            # the controls first: if either fails, nothing below means anything
            other = p2.evaluate("fetch('https://example.org/', {mode: 'no-cors'}).then(() => 'reached').catch(() => 'blocked')")
            rep.add("offline control: a cross-origin fetch is blocked", other == "blocked", other)
            same = p2.evaluate("fetch('offline-control-' + Date.now() + '.txt').then(r => 'server answered ' + r.status).catch(() => 'network failed')")
            rep.add("offline control: a same-origin fetch of a file that is not cached fails at the network", same == "network failed", same)
            flags = wait_ready(p2, 60_000)
            veil = p2.evaluate("document.getElementById('veil-msg')?.innerText || ''")
            rep.add("offline: the page starts with no network (window.deep.ready)", flags["ready"], (json.dumps(flags) + " " + veil)[:140])
            if not flags["ready"]:
                return
            p2.evaluate("document.getElementById('info-close')?.click()")
            n = p2.evaluate("window.deep.map.queryRenderedFeatures({layers:['places']}).length")
            rep.add("offline: place points still draw (basemap tiles are not expected)", n > 100, f"{n} features")
            if not wait_index(p2):
                rep.add("offline: name index loads from the cache", False)
                return
            before = p2.evaluate("window.deep.renders")
            p2.fill("#q", "Bunsty")
            wait_render(p2, before)
            txt = p2.evaluate("document.getElementById('drawer').innerText")
            rep.add("offline: search finds Bunsty Hundred", "Bunsty Hundred" in txt, txt[:60].replace("\n", " | "))
            bad = PLACEHOLDER.search(txt)   # the saved page code is the fixed one (16 Nov 2026: "undefined Bunsty Hundred")
            rep.add("offline: that result prints no placeholder word", "Bunsty Hundred" in txt and not bad, f"prints '{bad.group(0)}'" if bad else "")
            before = p2.evaluate("window.deep.renders")
            p2.fill("#q", "qzxqzxqzx")
            wait_render(p2, before)
            rep.add("offline: nonsense still finds nothing (paired with the hit above)", "No spelling matches" in p2.evaluate("document.getElementById('drawer').innerText"))
            # a county the page has not opened in THIS tab: its file must come from IndexedDB
            rep.add("offline: a field-name opens (its county file comes from the cache)", goto_id(p2, "28-e-fn-000001", 60_000))
            rep.add("offline: that record names its township", "Alfreton" in p2.evaluate("document.getElementById('drawer').innerText"))
            # the RDF view needs jsonld.js: vendored, so no CDN
            goto_id(p2, "02-hu-subcounty-000001")
            before = p2.evaluate("window.deep.renders")
            p2.evaluate("document.querySelector('.formats button[data-fmt=rdf]').click()")
            wait_render(p2, before, 60_000)
            body = p2.evaluate("document.getElementById('fmt-body').innerText")
            rep.add("offline: the N-Triples view builds (jsonld.js is local)", "<https://w3id.org/plato#" in body and "Could not build" not in body, body[:70].replace("\n", " "))
            p2.evaluate("document.getElementById('fmt-modal').hidden = true")
            # sound-alike: model, matrix and runtime from the cache
            p2.evaluate("document.getElementById('phon').click()")
            try:
                p2.wait_for_function("window.deep.state.phonReady === true", timeout=120_000)
                ok = True
            except Exception:
                ok = False
            rep.add("offline: phonetic model and index load from the cache", ok, p2.evaluate("document.getElementById('status')?.innerText || ''")[:80])
            if ok:
                before = p2.evaluate("window.deep.renders")
                p2.fill("#q", "Snotingaham")
                wait_render(p2, before)
                try:
                    p2.wait_for_function("(() => { const t = document.getElementById('drawer').innerText.toLowerCase(); return t.includes('snotingaham') && (t.includes('sounds like') || t.includes('nothing sounds') || document.querySelector('#drawer .rs .score')); })()", timeout=60_000)   # the query must be IN the drawer: the previous query's 'Nothing sounds…' is still there
                except Exception:
                    pass
                nsc = p2.evaluate("document.querySelectorAll('#drawer .rs .score').length")
                rep.add("offline: a sound-alike query returns scored candidates", nsc > 0, f"{nsc} scores; " + p2.evaluate("document.getElementById('drawer').innerText")[:160].replace("\n", " | "))
            p2.screenshot(path=str(OUT / "offline.png"))
            rep.add("offline: no uncaught page errors", not errs, "; ".join(errs)[:120])
        finally:
            ctx.close()
    finally:
        shutil.rmtree(prof, ignore_errors=True)


CHECKS = {"boot": None, "basemaps": check_basemaps, "search": check_search, "place": check_place, "formats": check_formats, "browse": check_browse, "placeholders": check_placeholders, "phonetic": check_phonetic, "ids": None, "downloads": None, "cache": None, "offline": None}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serve", action="store_true", help="serve docs/ on a loopback port and test that")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--url", default=None, help="test a deployed URL instead")
    ap.add_argument("--check", action="append", choices=list(CHECKS), help="run only these checks")
    ap.add_argument("--prove-it-fails", action="store_true", help="point every check at a page with no app; all must fail")
    ap.add_argument("--gl", action="store_true", help="launch chromium with the software-GL flag trio")
    args = ap.parse_args()
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    httpd = None
    if args.prove_it_fails:
        blank = OUT / "blank"
        blank.mkdir(exist_ok=True)
        (blank / "index.html").write_text("<!doctype html><title>no app</title><p>nothing here</p>")
        httpd = serve(blank, args.port)
        url = f"http://127.0.0.1:{args.port}/"
    elif args.serve:
        httpd = serve(ROOT / "docs", args.port)
        url = f"http://127.0.0.1:{args.port}/"
    elif args.url:
        url = args.url
    else:
        ap.error("one of --serve, --url or --prove-it-fails")

    wanted = args.check or list(CHECKS)
    rep = Report()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=GL if args.gl else [])
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        page = ctx.new_page()
        page.on("pageerror", lambda e: rep.add("page error", False, str(e)[:160]))
        t0 = time.time()
        booted = check_boot(page, rep, url) if "boot" in wanted or True else True
        if booted:
            for name in wanted:
                fn = CHECKS.get(name)
                if fn:
                    try:
                        fn(page, rep)
                    except Exception as e:  # a harness exception is a failure with a reason, not a crash
                        rep.add(f"{name}: harness exception", False, str(e)[:160])
            if "ids" in wanted:
                check_identifiers(page, rep, url)
            if "downloads" in wanted:
                check_downloads(page, rep, url)
            if "cache" in wanted:
                check_cache(page, rep, url)
            if "offline" in wanted:   # last: it cuts the network for the context
                try:
                    check_offline(pw, rep, url, httpd)
                except Exception as e:  # noqa: BLE001
                    rep.add("offline: harness exception", False, str(e)[:160])
        browser.close()
    if httpd:
        httpd.shutdown()

    n_fail = len(rep.failed)
    if args.prove_it_fails:
        n_pass = len(rep.rows) - n_fail
        print(f"\nprove-it-fails: {n_fail} failed, {n_pass} passed against a page with no app")
        sys.exit(0 if n_pass == 0 else 1)
    print(f"\n{len(rep.rows) - n_fail}/{len(rep.rows)} checks passed in {time.time() - t0:.0f}s; screenshots in {OUT.relative_to(ROOT)}/")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    os.chdir(ROOT)
    main()

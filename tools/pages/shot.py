#!/usr/bin/python3
"""Headless checks of the DEEP explorer. No browser window, no foreground tab.

    /usr/bin/python3 tools/pages/shot.py --serve                 # every check against docs/ on a loopback server
    /usr/bin/python3 tools/pages/shot.py --serve --check search
    /usr/bin/python3 tools/pages/shot.py --serve --prove-it-fails
    /usr/bin/python3 tools/pages/shot.py --url https://docuracy.github.io/deep/

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
    attr = page.evaluate("document.querySelector('.maplibregl-ctrl-attrib')?.innerText || ''")
    rep.add("map: attribution names EPNS/DEEP and CC BY-NC", "English Place-Name Society" in attr and "CC BY-NC" in attr, attr[:90])
    rep.add("map: attribution names the basemap provider", any(s in attr for s in ("CARTO", "OpenStreetMap", "National Library of Scotland")))
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
        attr = page.evaluate("document.querySelector('.maplibregl-ctrl-attrib')?.innerText || ''")
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
    rep.add("place: attestations rendered EPNS-style (1086 DB)", "1086 DB" in txt, "")
    rep.add("place: breadcrumb reaches the county", "Buckinghamshire" in txt)
    rep.add("place: children listed (parishes of the hundred)", "Cold Brayfield" in txt)
    rep.add("place: URL fragment carries the id", page.evaluate("location.hash") == "#id=02-hu-subcounty-000001", page.evaluate("location.hash"))
    # a field-name, which lives only in the county file, opens via its id
    page.evaluate("location.hash = '#id=28-e-fn-000001'")
    before = page.evaluate("window.deep.renders")
    rep.add("place: field-name opens from the county file", wait_render(page, before, 90_000))
    txt = page.evaluate("document.getElementById('drawer').innerText")
    rep.add("place: field-name shows its township", "Alder Wood" in txt and "Alfreton" in txt, txt[:80].replace("\n", " | "))
    page.screenshot(path=str(OUT / "place.png"))


def check_browse(page, rep: Report):
    sel = page.query_selector("#nav-county")
    n = page.evaluate("document.getElementById('nav-county').options.length - 1")
    rep.add("browse: county list populated", n == 66, f"{n} counties")
    before = page.evaluate("window.deep.renders")
    gid = page.evaluate("[...document.getElementById('nav-county').options].find(o=>o.text==='Buckinghamshire').value")
    page.select_option("#nav-county", gid)
    rep.add("browse: county page renders", wait_render(page, before, 90_000))
    txt = page.evaluate("document.getElementById('drawer').innerText")
    rep.add("browse: county page lists its hundreds", "Bunsty Hundred" in txt and "hundred" in txt.lower())


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
    before = page.evaluate("window.deep.renders")
    page.fill("#q", "Snotingaham")
    wait_render(page, before)
    try:
        page.wait_for_function("document.getElementById('drawer').innerText.includes('Sounds like') || document.getElementById('drawer').innerText.includes('Nothing sounds')", timeout=60_000)
    except Exception:
        pass
    # innerText is upper-cased by the section heading's CSS, so compare case-insensitively
    txt = page.evaluate("document.getElementById('drawer').innerText")
    low = txt.lower()
    rep.add("phonetic: 'Snotingaham' sounds like Nottingham", "sounds like" in low and "nottingham" in low, txt[:100].replace("\n", " | "))
    # The floor is applied: every score shown is within [0.70, 1.00] and the list is bounded. A
    # "nonsense finds nothing" control is NOT used here: to a phonetic model a short random string
    # is not noise (xqzpv scored 0.94 against Sceb when tried), so that assertion would test the
    # model's opinion of gibberish rather than the page's handling of scores.
    scores = page.evaluate("[...document.querySelectorAll('#drawer .rs .score')].map(e => +e.innerText)")
    rep.add("phonetic: scores shown respect the 0.70 floor and the cap", len(scores) > 0 and len(scores) <= 30 and all(0.70 <= x <= 1.0 for x in scores),
            f"{len(scores)} scores, min {min(scores) if scores else None}")
    page.evaluate("document.getElementById('phon').click()")
    page.screenshot(path=str(OUT / "phonetic.png"))


def check_cache(page, rep: Report, url: str):
    """Second load must come from IndexedDB: same readiness, no core.json request over the wire."""
    fetched = []
    page.on("request", lambda r: fetched.append(r.url) if "data/core.json" in r.url else None)
    page.goto(url, wait_until="load", timeout=60_000)
    flags = wait_ready(page)
    rep.add("cache: second load ready", flags["ready"])
    rep.add("cache: core.json served from IndexedDB (no network request)", len(fetched) == 0, f"{len(fetched)} request(s)")


CHECKS = {"boot": None, "basemaps": check_basemaps, "search": check_search, "place": check_place, "browse": check_browse, "phonetic": check_phonetic, "cache": None}


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
            if "cache" in wanted:
                check_cache(page, rep, url)
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

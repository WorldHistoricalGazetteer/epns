#!/usr/bin/env python3
"""Write docs/downloads.html from the export manifests, and package the analytic files.

    .venv/bin/python process/build_downloads.py --tag data-2026-09-26        # page + bundles under data/export/
    .venv/bin/python process/build_downloads.py --tag data-2026-09-26 --check # confirm the release assets exist

Every size and digest on the page is read from data/export/manifest.json (written by export_plato.py)
or computed here from the file; nothing is typed in. The page therefore cannot describe a file
that was not built, and a rebuild that changes a file changes the page.

The whole-corpus files are GitHub Release assets (they are too large and too rarely rebuilt to
belong in git history); the URL pattern is fixed by GitHub, and --check asks the API whether
each named asset is really there before the page claims it is.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "data" / "export"
REPO = "docuracy/deep"
LICENCE_TEXT = ("Digitisation of English Placenames MADS data is licensed to Jisc by the English Place Names Society and "
                "released under a Creative Commons Attribution-NonCommercial 4.0 International License.")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def mb(n: int) -> str:
    return f"{n / 1e6:,.1f} MB" if n >= 1e6 else f"{n / 1e3:,.0f} KB"


def bundle_analytic() -> list[dict]:
    """Parquet tables as one tar (already zstd-compressed inside), the DuckDB file gzipped."""
    out = []
    tar = EXP / "deep-parquet.tar"
    with tarfile.open(tar, "w") as t:
        for p in sorted((ROOT / "data" / "parquet").glob("*.parquet")):
            t.add(p, arcname=f"parquet/{p.name}")
    out.append({"file": tar.name, "bytes": tar.stat().st_size, "sha256": sha256(tar)})
    for name, sub in (("deep-plato-counties.tar", "plato"), ("deep-lpf-counties.tar", "lpf")):
        t = EXP / name
        with tarfile.open(t, "w") as tf:
            for p in sorted((EXP / sub).glob("*")):
                tf.add(p, arcname=f"{sub}/{p.name}")
        out.append({"file": name, "bytes": t.stat().st_size, "sha256": sha256(t)})
    gz = EXP / "deep.duckdb.gz"
    with open(ROOT / "data" / "deep.duckdb", "rb") as src, gzip.open(gz, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    out.append({"file": gz.name, "bytes": gz.stat().st_size, "sha256": sha256(gz)})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", required=True, help="the GitHub release tag the assets are (to be) attached to")
    ap.add_argument("--check", action="store_true", help="verify every asset named on the page exists in the release")
    ap.add_argument("--no-bundle", action="store_true", help="do not rebuild the parquet tar / duckdb gz")
    args = ap.parse_args()

    man = json.loads((EXP / "manifest.json").read_text())
    counts = json.loads((ROOT / "docs" / "data" / "manifest.json").read_text())["counts"]
    base = f"https://github.com/{REPO}/releases/download/{args.tag}/"
    files = {f["file"]: f for f in man["files"]}
    analytic = json.loads((EXP / "analytic.json").read_text()) if (EXP / "analytic.json").exists() and args.no_bundle else bundle_analytic()
    (EXP / "analytic.json").write_text(json.dumps(analytic, indent=1))
    plato_whole = files["deep-plato.jsonl.gz"]
    lpf_whole = files["deep-lpf.geojsonl.gz"]
    per_county_plato = sum(f["bytes"] for k, f in files.items() if k.startswith("plato/"))
    per_county_lpf = sum(f["bytes"] for k, f in files.items() if k.startswith("lpf/"))
    located = counts["place_located_own"]
    total = counts["place"]
    null_pct = 100 * (total - located) / total

    if args.check:
        rel = json.loads(subprocess.check_output(["gh", "release", "view", args.tag, "-R", REPO, "--json", "assets"], text=True))
        have = {a["name"]: a for a in rel["assets"]}
        want = ["deep-plato.jsonl.gz", "deep-lpf.geojsonl.gz", "deep-parquet.tar", "deep.duckdb.gz", "export-manifest.json", "deep-plato-counties.tar", "deep-lpf-counties.tar"]
        bad = [w for w in want if w not in have]
        if bad:
            sys.exit(f"release {args.tag} is missing: {bad}")
        for w in want:
            local = EXP / ("manifest.json" if w == "export-manifest.json" else w)
            if local.exists() and have[w]["size"] != local.stat().st_size:
                sys.exit(f"release asset {w} has size {have[w]['size']}, local {local.stat().st_size}")
        print(f"release {args.tag}: all {len(want)} assets present with matching sizes")

    # Anonymous reachability: a private repository serves release assets only to people with access,
    # and the page must not imply otherwise. Checked without credentials, the way a visitor arrives.
    import urllib.request
    anon_ok, anon_err = False, ""
    try:
        req = urllib.request.Request(base + "export-manifest.json", method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as resp:
            anon_ok = resp.status == 200
    except Exception as e:  # noqa: BLE001
        anon_err = str(getattr(e, "code", e))
    access_note = "" if anon_ok else (
        '<div class="caveat"><b>Access, ' + time.strftime("%d %B %Y") + ':</b> the repository that holds these release files is '
        'private, so the links below answer HTTP ' + anon_err + ' to anyone not signed in with access. The site is public but the '
        'files are not yet; when the repository is made public the same links will work unchanged. Until then, ask via '
        f'<a href="https://github.com/{REPO}/issues">the issue tracker</a> or the contact on the map\'s information panel.</div>')
    print("anonymous access to release assets:", "yes" if anon_ok else f"no ({anon_err}); access note added")

    commit_link = f'<a href="https://github.com/pelagios/place-attestation-ontology/commit/{man["plato_commit"]}"><code>{man["plato_commit"][:12]}</code></a>'
    prov = (f'generated and schema-validated against PLATO <a href="https://github.com/pelagios/place-attestation-ontology/releases/tag/{man["plato_tag"]}">{man["plato_tag"]}</a> '
            f'(release {man["plato_versionInfo_at_commit"]}, commit {commit_link}); the Citation class, source dating, occurrence and form status this export uses are first released in that version'
            if man.get("plato_tag") else
            f'generated and validated against PLATO commit {commit_link}. That commit\'s <code>owl:versionInfo</code> reads <code>{man["plato_versionInfo_at_commit"]}</code>, which is not the provenance: '
            f'the terms this export uses are on <code>main</code> and in no release')

    row = lambda name, desc, f: f'<tr><td><a href="{base}{name}">{name}</a></td><td>{desc}</td><td class="num">{mb(f["bytes"])}</td><td class="sha">{f["sha256"][:16]}…</td></tr>'
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>DEEP downloads — the English place-name survey as data</title>
  <link rel="icon" type="image/svg+xml" href="favicon.svg" />
  <link rel="stylesheet" href="css/app.css?v=4" />
  <style>
    body {{ background: var(--paper); overflow: auto; }}
    main {{ max-width: 880px; margin: 0 auto; padding: 1.4rem 1.2rem 3rem; font-size: .95rem; line-height: 1.55; }}
    main h1 {{ font-size: 1.5rem; margin: .2rem 0 .2rem; }} main h2 {{ font-size: 1.1rem; margin: 1.6rem 0 .4rem; color: #6b4a2a; }}
    main table {{ border-collapse: collapse; width: 100%; font-size: .86rem; margin: .5rem 0 .3rem; }}
    main td, main th {{ padding: .35rem .5rem; border-bottom: 1px solid var(--line); vertical-align: top; text-align: left; }}
    main .num {{ white-space: nowrap; text-align: right; }} main .sha {{ font-family: ui-monospace, monospace; font-size: .74rem; color: var(--muted); }}
    .caveat {{ margin: .6rem 0; padding: .6rem .8rem; background: #f3e9d9; border-left: 3px solid #a24a2a; border-radius: 4px; }}
    .licence {{ margin: .8rem 0; padding: .6rem .8rem; background: #efeadf; border-left: 3px solid #b99a5a; border-radius: 4px; font-size: .9rem; }}
    .back {{ font-family: system-ui, sans-serif; font-size: .82rem; }}
    code {{ font-size: .84em; }}
  </style>
</head>
<body>
<main>
  <p class="back"><a href="./">‹ Back to the map</a></p>
  <h1>Downloads</h1>
  <p>The whole corpus, in three shapes for three purposes. Every file below was generated from the same DuckDB build of the DEEP XML on {man["generated"][:10]}; sizes and digests are read from the build manifest, not typed.</p>
  {access_note}
  <div class="licence">{LICENCE_TEXT}<br /><span class="small">Every file on this page derives from that data and carries the same terms. The PLATO and LPF serialisations are adaptations of it and therefore cannot be offered under CC BY or CC0.</span></div>

  <h2>1. PLATO — lossless</h2>
  <p>Every element of every record: {man["entities"]:,} SpatialEntities and {man["attestations"]:,} attestations, with dated citations, locators, copy-dates on witness sources, occurrence counts and contexts, headword and normalised form status, coordinates per gazetteer and GeoNames identity relations. This is the format to use if you want the attestations.</p>
  <table><tr><th>File</th><th>What</th><th>Size</th><th>sha256</th></tr>
  {row("deep-plato.jsonl.gz", "JSON Lines: a header line (gazetteer + provenance), then one <code>spatialEntity</code> per line, then the identityRelations. Reassembles into one place-centric document.", plato_whole)}
  {row("export-manifest.json", "The build manifest: PLATO commit, digests of every file, counts.", {"bytes": (EXP / "manifest.json").stat().st_size, "sha256": sha256(EXP / "manifest.json")})}
  {row("deep-plato-counties.tar", "The 66 per-county place-centric documents (gzipped JSON), each a complete, schema-valid PLATO file for one survey volume.", analytic[1])}
  </table>
  <p class="small"><b>Provenance:</b> {prov}. Each file carries <code>plato_commit=</code> in its header and <code>export_plato.py --verify</code> fails if any file and the manifest disagree. Schema errors at build: {man["schema_errors"]}.</p>

  <h2>2. Parquet and DuckDB — for analysis</h2>
  <p>The tables the site and the exports are built from: <code>place</code>, <code>name</code>, <code>attestation</code>, <code>attestation_date</code>, <code>passim</code>, <code>searchterm</code>, <code>geo</code>, <code>note</code>, plus the derived <code>source</code>, <code>place_tree</code> and <code>place_point</code>. If you are counting, joining or plotting, this is what you want.</p>
  <table><tr><th>File</th><th>What</th><th>Size</th><th>sha256</th></tr>
  {row("deep-parquet.tar", "Eight Parquet tables (zstd), one per element type; readable by DuckDB, pandas, Polars, R, Spark.", analytic[0])}
  {row("deep.duckdb.gz", "The DuckDB database with all eleven tables. <code>gunzip</code>, then <code>duckdb deep.duckdb</code>.", analytic[3])}
  </table>
  <p class="small">Schema and example queries are in the repository <a href="https://github.com/{REPO}#the-database">README</a>.</p>

  <h2>3. Linked Places Format — interoperability, with a caveat</h2>
  <div class="caveat"><b>It is valid GeoJSON, so QGIS, ArcGIS and any GIS will open it. Before you do:</b> only <b>{located:,} of {total:,}</b> records ({100 - null_pct:.1f}%) carry coordinates, so <b>{null_pct:.1f}% of features have <code>null</code> geometry</b> and will not appear on a map at all. Of those with coordinates, most carry two to four points from different gazetteers as a <code>GeometryCollection</code>, which some GIS software flattens or refuses. The {total - located:,} unlocated features are still useful: they carry the hierarchy in <code>relations[]</code>, so a township's field-names can be placed by their parent.</div>
  <p>LPF v1.3 cannot hold the attestations without loss. In this export: a citation keeps a label and a single year, so the editors' date brackets, regnal and <i>circa</i> semantics collapse; page, folio, item and manuscript references, copy-dates, the <i>(p)</i> personal-name marker, occurrence counts and the italic-for-manuscript convention are dropped; <code>citations[]</code> and <code>when</code> sit in parallel arrays with no link between a source and its span; <i>et passim</i> runs become separate citations; names have no URIs; the {counts["searchterm"]:,} normalised search forms are omitted rather than conflated with attested spellings; record ids and creation dates have no slot. The eight classes of loss and why they matter are set out in <a href="https://github.com/LinkedPasts/linked-places-format/discussions/53">LPF discussion 53</a>. <b>If you want the attestations, use the PLATO file.</b> Each place page on the map shows this record's LPF with its losses struck through in place.</p>
  <table><tr><th>File</th><th>What</th><th>Size</th><th>sha256</th></tr>
  {row("deep-lpf.geojsonl.gz", "GeoJSON Text Sequence: a header line, then one LPF Feature per line. Wrap in a FeatureCollection, or load directly with GDAL's GeoJSONSeq driver.", lpf_whole)}
  {row("deep-lpf-counties.tar", "The 66 per-county FeatureCollections (gzipped GeoJSON), each openable in a GIS on its own.", analytic[2])}
  </table>

  <h2>Citing</h2>
  <p>Survey data: English Place-Name Society, digitised by DEEP (2011–13) and published by Jisc (2017). Cite the county volume for any individual name; each place page shows the DEEP record identifier. Conversion and site: Gadd, Stephen. 2026. <i>DEEP — the English place-name survey, as data and as a map</i>. <a href="https://github.com/{REPO}">github.com/{REPO}</a>, release <code>{args.tag}</code>.</p>
</main>
</body>
</html>
'''
    (ROOT / "docs" / "downloads.html").write_text(html)
    print(f"wrote docs/downloads.html for release {args.tag}: PLATO {mb(plato_whole['bytes'])}, LPF {mb(lpf_whole['bytes'])}, parquet {mb(analytic[0]['bytes'])}, duckdb {mb(analytic[3]['bytes'])}; null geometry {null_pct:.1f}%")


if __name__ == "__main__":
    main()

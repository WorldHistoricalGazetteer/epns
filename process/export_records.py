#!/usr/bin/env python3
"""Per-record static representations for the persistent-identifier tier: docs/id/<county>/<serial>.{json,geojson,xml}

    .venv/bin/python process/export_records.py            # records at parish level and above (15,587 x 3 files)
    .venv/bin/python process/export_records.py --all      # every non-field-name record (160,829 x 3; not deployed)

WHY A TIER, AND WHICH CUT. A persistent URL that content-negotiates has to land on a file that exists,
and GitHub Pages serves statics only. Three machine formats for all 539,372 records would be 1.6
million files; for the 160,829 non-field-name records, 482,000. The corpus is top-light: 15,587
records sit at parish level and above (counties, hundreds and their kin, parishes, boroughs, county
towns, townships, chapelries), and those are the records anyone cites by identifier. So the cut is:

  parish level and above   three static files each (PLATO document, LPF FeatureCollection, MADS)
  minor names, field-names no static machine file; the HTML view (#u=<county>/<serial>) is a
                           fragment on one page and costs nothing, and it generates the same three
                           formats in the browser. A machine request answers HTTP 404, honestly,
                           with a page that carries a person to the record.

The serial is DEEP's own county-wide number from the record's URI (placenames.org.uk/id/placename/
<county>/<serial>), not the per-type `seq`: it is unique within a county for all 539,372 records.

WHAT EACH FILE IS. The PLATO file is a complete place-centric document for one SpatialEntity, with
the same gazetteer block and PLATO-commit stamp as the corpus export, so it validates on its own and
says what it was validated against. The LPF file is a FeatureCollection of one Feature with the LPF
context. The MADS file is the ORIGINAL <mads> element from the DEEP source file, serialised by lxml
(not the regenerated form the site shows), with an XML declaration.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import duckdb
from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_plato import DB, LICENCE_TEXT, Exporter, PLATO_REPO, STAMP_RE, git_show, load_validator  # noqa: E402
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "mads2017"
OUT = ROOT / "docs" / "id"
UPPER = ("county", "province", "subcounty", "dbhundred", "halfhundred", "liberty", "abovesubcounty", "belowsubcounty",
         "localdistrict", "parish", "borough", "countytown", "subparish", "chapelry", "belowparish", "forest", "feature")
LPF_CONTEXT = "https://raw.githubusercontent.com/LinkedPasts/linked-places-format/main/linkedplaces-context-v1.1.jsonld"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="every non-field-name record, not only parish level and above")
    ap.add_argument("--plato-commit", default=None)
    ap.add_argument("--validate", action="store_true", help="validate every per-record PLATO document (slow)")
    args = ap.parse_args()
    sha = args.plato_commit or json.loads((ROOT / "data" / "export" / "manifest.json").read_text())["plato_commit"]
    version_info = re.search(r'owl:versionInfo\s+"([^"]+)"', git_show(sha, "ontology.ttl")).group(1)
    tags = subprocess.check_output(["git", "-C", str(PLATO_REPO), "tag", "--points-at", sha], text=True).split()
    tag = next((t for t in tags if t.lstrip("v") == version_info), None)
    validator = load_validator(sha) if args.validate else None

    con = duckdb.connect(str(DB), read_only=True)
    ex = Exporter(sha, con, tag, version_info)
    where = "type <> 'fn'" if args.all else "type IN " + str(UPPER)
    wanted = dict(con.execute(f"SELECT place_id, title_uri FROM place WHERE {where}").fetchall())
    serial_of = {pid: uri.rsplit("/", 1)[1] for pid, uri in wanted.items()}
    print(f"{len(wanted):,} records -> {OUT}", flush=True)
    if OUT.exists():
        import shutil
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    # 1. MADS: the original elements, straight from the source files
    t0 = time.time()
    n_xml = 0
    for f in sorted(SRC.glob("vol*.xml")):
        for _, mads in etree.iterparse(str(f), events=("end",), tag="mads"):
            pid = mads.get("ID")
            if pid in wanted:
                cc, serial = pid.split("-")[2], serial_of[pid]
                d = OUT / cc
                d.mkdir(exist_ok=True)
                (d / f"{serial}.xml").write_bytes(b'<?xml version="1.0" encoding="UTF-8"?>\n'
                                                  b"<!-- The original <mads> element from " + f.name.encode() + b" (DEEP, Jisc 2017 release). "
                                                  + LICENCE_TEXT.encode() + b" -->\n" + etree.tostring(mads, encoding="utf-8"))
                n_xml += 1
            mads.clear()
            while mads.getprevious() is not None:
                del mads.getparent()[0]
    print(f"  {n_xml:,} MADS files in {time.time() - t0:.0f}s", flush=True)

    # 2. PLATO + LPF, county by county from the same exporter the corpus files come from
    n_json = 0
    errs = 0
    for cc in sorted(ex.county_name):
        doc = ex.county_document(cc)
        idr_by = defaultdict(list)
        for r in doc.get("identityRelations", []):
            idr_by[r["subject"]].append(r)
        gaz = doc["gazetteer"]
        for ent in doc["spatialEntities"]:
            m = re.search(r"DEEP record (epns-deep-[^;]+);", ent["attestations"][0]["notes"])
            pid = m.group(1) if m else None
            if pid not in wanted:
                continue
            serial = serial_of[pid]
            one = {"$schema": doc["$schema"], "profile": "place-centric", "gazetteer": gaz, "spatialEntities": [ent]}
            if idr_by.get(ent["@id"]):
                one["identityRelations"] = idr_by[ent["@id"]]
            if validator:
                e = next(validator.iter_errors(one), None)
                if e:
                    errs += 1
                    if errs <= 5:
                        print(f"  SCHEMA {pid}: {e.message[:120]}")
            d = OUT / cc
            d.mkdir(exist_ok=True)
            (d / f"{serial}.json").write_text(json.dumps(one, ensure_ascii=False, separators=(",", ":")))   # compact: 326 -> 242 MB over the tier
            feat = ex.lpf_feature(ent, idr_by.get(ent["@id"], []))
            (d / f"{serial}.geojson").write_text(json.dumps({"@context": LPF_CONTEXT, "type": "FeatureCollection", "license": LICENCE_TEXT,
                                                               "note": f"LPF v1.3, lossy; derived from the PLATO record; plato_commit={sha}", "features": [feat]},
                                                              ensure_ascii=False, separators=(",", ":")))
            n_json += 1
        print(f"  {cc} {ex.county_name.get(cc, ''):28s} done  {time.time() - t0:4.0f}s", flush=True)
    total = sum(1 for _ in OUT.rglob("*.*"))
    size = sum(p.stat().st_size for p in OUT.rglob("*.*"))
    (OUT / "manifest.json").write_text(json.dumps({"plato_commit": sha, "plato_tag": tag, "tier": "all non-field-name records" if args.all else "parish level and above",
                                                   "records": n_json, "files": total, "bytes": size, "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, indent=1))
    print(f"{n_json:,} records, {total:,} files, {size / 1e6:.0f} MB; schema errors {errs}; plato_commit {sha[:12]}{' ' + tag if tag else ''}; {time.time() - t0:.0f}s")
    if n_xml != n_json:
        sys.exit(f"MADS files ({n_xml}) and PLATO files ({n_json}) disagree")
    if errs:
        sys.exit(2)


if __name__ == "__main__":
    main()

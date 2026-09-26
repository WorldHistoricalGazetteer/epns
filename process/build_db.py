#!/usr/bin/env python3
"""Parse the DEEP MADS XML into Parquet tables and a DuckDB database.

    .venv/bin/python process/build_db.py                 # data/mads2017/*.xml -> data/parquet/ + data/deep.duckdb
    .venv/bin/python process/build_db.py --limit 3       # first three volumes only (smoke test)

WHAT THE SOURCE IS. 66 files, one per English Place-Name Society survey volume, in MADS XML
(the Library of Congress authority schema) as published by Jisc's DEEP project in 2013 and
mirrored into data/mads2017/ by process/fetch_data.sh. Each <mads> is one place: a headword
(<authority>), one <related type="broader"> parent, optional <variant> spellings, and an
<extension> holding the scholarly content — dated <attestation>s of each spelling, normalised
<searchterm>s, and <geo> points from up to four gazetteers.

WHY A PARSER AND NOT A GENERIC XML-TO-TABLE TOOL. The interesting structure is irregular in
ways a generic flattener gets wrong: an attestation may carry several dates, may cite several
variants at once (space-separated IDs, 259 cases), may be NESTED inside another attestation
(26,327 cases, the survey's "et passim" runs), and sits in document order beside <passim>
text that belongs to the run rather than to any one attestation. Empty date attributes mean
"no date", not year zero. All of that is made explicit here rather than left for every
downstream query to rediscover.

THE COUNTS ARE CHECKED TWO WAYS. After parsing, the totals are compared with counts made by
a byte-level regex over the same files. The regex is deliberately a different instrument
from the XML parser: if the two agree the parse read every record, and if they disagree the
run fails rather than publishing a table that is quietly short. An earlier profile of this
corpus (26 Sep 2026) found 539,372 places, 820,567 name forms, 429,536 attestations and
441,237 dates; those figures are asserted below as a third, independent check, and the
--no-expect flag drops them for a re-run on different source files.

OUTPUT. data/parquet/{place,name,attestation,attestation_date,passim,searchterm,geo,note}.parquet
and data/deep.duckdb, which attaches the Parquet files as tables and adds derived views:
source (the abbreviation registry reconstructed from the attestations), place_tree (each
place's county / hundred / parish / township ancestors) and place_point (one preferred
coordinate per place, own or inherited).
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "mads2017"
OUT = ROOT / "data" / "parquet"
DB = ROOT / "data" / "deep.duckdb"

XLINK = "{http://www.w3.org/1999/xlink}href"
ID_RE = re.compile(r"^epns-deep-(\d+)-([a-z0-9]+)-([a-z]+)-(\d+)$")
AUTH_RE = re.compile(r"^epns-deep-\d+-[a-z0-9]+-name-([a-z]+)-\d+$")

# Independent expectations from the 26 Sep 2026 profile of the published files.
EXPECT = {"place": 539_372, "name": 820_567, "attestation": 429_536, "attestation_date": 441_237,
          "geo": 53_657, "searchterm": 391_777}

# Attestation children that are carried as columns. <date> and nested <attestation> are handled
# separately; anything not listed here is reported at the end so a new element cannot pass unseen.
ATT_TEXT_COLS = ["page", "item", "foliono", "ms", "pername", "entryno", "appendixno", "noteno",
                 "number", "times"]


def to_float(s: str | None) -> float | None:
    """A coordinate, or None. The source has at least one latitude written '54.27454.27068'
    (two values run together); that is nulled here, and the raw string is kept beside it."""
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_int(s: str | None) -> int | None:
    if s is None:
        return None
    s = s.strip()
    if not s or not s.lstrip("-").isdigit():
        return None
    return int(s)


class Tables:
    """Column-oriented buffers, flushed to Parquet once per volume to bound memory."""

    def __init__(self):
        self.rows = {k: [] for k in ["place", "name", "attestation", "attestation_date",
                                     "passim", "searchterm", "geo", "note"]}
        self.written = {k: 0 for k in self.rows}
        self.writers: dict[str, pq.ParquetWriter] = {}
        self.unknown_children: dict[str, int] = {}
        self.bad_coords: list[tuple] = []

    def flush(self):
        for k, rows in self.rows.items():
            if not rows:
                continue
            table = pa.Table.from_pylist(rows, schema=SCHEMAS[k])
            if k not in self.writers:
                self.writers[k] = pq.ParquetWriter(OUT / f"{k}.parquet", SCHEMAS[k], compression="zstd")
            self.writers[k].write_table(table)
            self.written[k] += len(rows)
            rows.clear()

    def close(self):
        self.flush()
        for w in self.writers.values():
            w.close()


SCHEMAS = {
    "place": pa.schema([
        ("place_id", pa.string()), ("county_code", pa.string()), ("type_code", pa.string()),
        ("type", pa.string()), ("seq", pa.int32()), ("title", pa.string()), ("title_uri", pa.string()),
        ("auth_type", pa.string()), ("parent_id", pa.string()), ("parent_title", pa.string()),
        ("content_source", pa.string()), ("created", pa.string()), ("volume", pa.string()),
        ("n_variants", pa.int32()), ("n_attestations", pa.int32()), ("n_geo", pa.int32()),
    ]),
    "name": pa.schema([
        ("name_id", pa.string()), ("place_id", pa.string()), ("toponym", pa.string()),
        ("uri", pa.string()), ("kind", pa.string()),
    ]),
    "attestation": pa.schema([
        ("attestation_id", pa.int64()), ("place_id", pa.string()), ("pos", pa.int32()),
        ("parent_attestation_id", pa.int64()), ("depth", pa.int16()),
        ("variant_ids", pa.list_(pa.string())), ("source_id", pa.string()), ("source_text", pa.string()),
        ("source_style", pa.string()), ("source_underspec", pa.string()),
        ("copydate_text", pa.string()), ("copydate_begin", pa.int32()), ("copydate_end", pa.int32()),
        *[(c, pa.string()) for c in ATT_TEXT_COLS],
        ("n_dates", pa.int16()),
    ]),
    "attestation_date": pa.schema([
        ("attestation_id", pa.int64()), ("seq", pa.int16()), ("subtype", pa.string()),
        ("begin", pa.int32()), ("end", pa.int32()), ("text", pa.string()),
    ]),
    "passim": pa.schema([("place_id", pa.string()), ("pos", pa.int32()), ("text", pa.string())]),
    "searchterm": pa.schema([
        ("place_id", pa.string()), ("variant_id", pa.string()), ("mads_id", pa.string()), ("term", pa.string()),
    ]),
    "geo": pa.schema([
        ("place_id", pa.string()), ("source", pa.string()), ("lon", pa.float64()), ("lat", pa.float64()),
        ("easting", pa.int32()), ("northing", pa.int32()), ("gazref", pa.string()),
        ("kepnref", pa.string()), ("kepnexref", pa.string()),
        ("lon_raw", pa.string()), ("lat_raw", pa.string()),
    ]),
    "note": pa.schema([("place_id", pa.string()), ("mads_id", pa.string()), ("text", pa.string())]),
}


def text(el) -> str | None:
    if el is None or el.text is None:
        return None
    t = el.text.strip()
    return t or None


def parse_volume(path: Path, t: Tables, next_att_id: int) -> int:
    volume = path.name
    for _, mads in etree.iterparse(str(path), events=("end",), tag="mads"):
        pid = mads.get("ID")
        m = ID_RE.match(pid or "")
        county, code, ptype, seq = (m.groups() if m else (None, None, None, None))
        auth = mads.find("authority")
        auth_geo = auth.find("geographic") if auth is not None else None
        am = AUTH_RE.match(auth.get("ID", "") if auth is not None else "")
        rel = mads.find("related")
        rel_geo = rel.find("geographic") if rel is not None else None
        parent = rel.get(XLINK) if rel is not None else None
        info = mads.find("recordInfo")
        created = text(info.find("recordCreationDate")) if info is not None else None
        csrc = info.find("recordContentSource") if info is not None else None
        ext = mads.find("extension")

        # names: the headword, then each variant
        t.rows["name"].append({"name_id": auth.get("ID") if auth is not None else None, "place_id": pid,
                               "toponym": text(auth_geo), "uri": auth_geo.get("valueURI") if auth_geo is not None else None,
                               "kind": "authority"})
        variants = mads.findall("variant")
        for v in variants:
            vg = v.find("geographic")
            t.rows["name"].append({"name_id": v.get("ID"), "place_id": pid, "toponym": text(vg),
                                   "uri": vg.get("valueURI") if vg is not None else None, "kind": "variant"})

        n_att = 0
        n_geo = 0
        if ext is not None:
            pos = 0

            def walk(att, parent_att_id, depth):
                nonlocal next_att_id, pos, n_att
                aid = next_att_id
                next_att_id += 1
                pos += 1
                n_att += 1
                row = {"attestation_id": aid, "place_id": pid, "pos": pos, "parent_attestation_id": parent_att_id,
                       "depth": depth, "variant_ids": (att.get("variantID") or "").split(),
                       "source_id": None, "source_text": None, "source_style": None, "source_underspec": None,
                       "copydate_text": None, "copydate_begin": None, "copydate_end": None, "n_dates": 0}
                for c in ATT_TEXT_COLS:
                    row[c] = None
                dseq = 0
                for child in att:
                    tag = child.tag
                    if tag == "date":
                        dseq += 1
                        t.rows["attestation_date"].append({
                            "attestation_id": aid, "seq": dseq, "subtype": child.get("subtype"),
                            "begin": to_int(child.get("begin")), "end": to_int(child.get("end")), "text": text(child)})
                    elif tag == "source":
                        row["source_id"] = child.get("id")
                        row["source_text"] = text(child)
                        row["source_style"] = child.get("style") or None
                        row["source_underspec"] = child.get("underspec")
                    elif tag == "copydate":
                        row["copydate_text"] = text(child)
                        row["copydate_begin"] = to_int(child.get("begin"))
                        row["copydate_end"] = to_int(child.get("end"))
                    elif tag == "attestation":
                        walk(child, aid, depth + 1)
                    elif tag == "passim":
                        t.rows["passim"].append({"place_id": pid, "pos": pos, "text": text(child)})
                    elif tag in ATT_TEXT_COLS:
                        row[tag] = text(child)
                    else:
                        t.unknown_children[tag] = t.unknown_children.get(tag, 0) + 1
                row["n_dates"] = dseq
                t.rows["attestation"].append(row)

            for child in ext:
                tag = child.tag
                if tag == "attestation":
                    walk(child, None, 0)
                elif tag == "passim":
                    pos += 1
                    t.rows["passim"].append({"place_id": pid, "pos": pos, "text": text(child)})
                elif tag == "searchterm":
                    t.rows["searchterm"].append({"place_id": pid, "variant_id": child.get("variantID"),
                                                 "mads_id": child.get("madsID"), "term": text(child)})
                elif tag == "geo":
                    n_geo += 1
                    lon, lat = to_float(child.get("long")), to_float(child.get("lat"))
                    if (lon is None) != (child.get("long") is None) or (lat is None) != (child.get("lat") is None):
                        t.bad_coords.append((pid, child.get("long"), child.get("lat")))
                    t.rows["geo"].append({
                        "place_id": pid, "source": child.get("source"), "lon": lon, "lat": lat,
                        "easting": to_int(child.get("easting")), "northing": to_int(child.get("northing")),
                        "gazref": child.get("gazref"), "kepnref": child.get("kepnref"), "kepnexref": child.get("kepnexref"),
                        "lon_raw": child.get("long") if lon is None and child.get("long") else None,
                        "lat_raw": child.get("lat") if lat is None and child.get("lat") else None})
                elif tag == "note":
                    t.rows["note"].append({"place_id": pid, "mads_id": child.get("madsID"), "text": text(child)})
                else:
                    t.unknown_children["extension/" + tag] = t.unknown_children.get("extension/" + tag, 0) + 1

        t.rows["place"].append({
            "place_id": pid, "county_code": county, "type_code": code, "type": ptype,
            "seq": int(seq) if seq else None, "title": text(auth_geo),
            "title_uri": auth_geo.get("valueURI") if auth_geo is not None else None,
            "auth_type": am.group(1) if am else None,
            "parent_id": parent[1:] if parent and parent.startswith("#") else parent,
            "parent_title": text(rel_geo),
            "content_source": csrc.get("valueURI") if csrc is not None else None,
            "created": created, "volume": volume,
            "n_variants": len(variants), "n_attestations": n_att, "n_geo": n_geo})
        mads.clear()
        while mads.getprevious() is not None:
            del mads.getparent()[0]
    t.flush()
    return next_att_id


def regex_counts(files: list[Path]) -> dict[str, int]:
    """A second instrument: byte-level tag counts that share nothing with the XML parser."""
    pats = {"place": re.compile(rb"<mads ID="), "variant": re.compile(rb"<variant ID="),
            "attestation": re.compile(rb"<attestation"), "date": re.compile(rb"<date "),
            "geo": re.compile(rb"<geo "), "searchterm": re.compile(rb"<searchterm ")}
    out = {k: 0 for k in pats}
    for f in files:
        data = f.read_bytes()
        for k, p in pats.items():
            out[k] += len(p.findall(data))
    return out


def build_duckdb(counts: dict[str, int]):
    if DB.exists():
        DB.unlink()
    con = duckdb.connect(str(DB))
    for k in SCHEMAS:
        con.execute(f"CREATE TABLE {k} AS SELECT * FROM read_parquet('{(OUT / (k + '.parquet')).as_posix()}')")
    # The source registry was never published; reconstruct it from usage. An id can carry more than
    # one text (331 cases), so the modal text is kept and the count of distinct texts is exposed.
    con.execute("""
        CREATE TABLE source AS
        WITH u AS (
            SELECT p.county_code, a.source_id, a.source_text, a.source_style, count(*) AS n
            FROM attestation a JOIN place p USING (place_id)
            WHERE a.source_id IS NOT NULL
            GROUP BY ALL)
        SELECT county_code, source_id,
               arg_max(source_text, n) AS source_text,
               arg_max(source_style, n) AS source_style,
               count(*) AS n_texts, sum(n) AS n_attestations
        FROM u GROUP BY county_code, source_id
    """)
    # Ancestors: every place's chain to its county, materialised so the SPA export and any query
    # can group by hundred or parish without a recursive CTE each time.
    con.execute("""
        CREATE TABLE place_tree AS
        WITH RECURSIVE up AS (
            SELECT place_id, place_id AS anc_id, 0 AS steps FROM place
            UNION ALL
            SELECT up.place_id, p.parent_id, up.steps + 1
            FROM up JOIN place p ON p.place_id = up.anc_id
            WHERE p.parent_id IS NOT NULL AND up.steps < 12)
        SELECT u.place_id,
               max(CASE WHEN a.type = 'county' THEN a.place_id END) AS county_id,
               max(CASE WHEN a.type IN ('subcounty','dbhundred','liberty','halfhundred','abovesubcounty','belowsubcounty','localdistrict') AND u.steps > 0 THEN a.place_id END) AS hundred_id,
               max(CASE WHEN a.type IN ('parish','borough','countytown') AND u.steps > 0 THEN a.place_id END) AS parish_id,
               max(CASE WHEN a.type IN ('subparish','belowparish','chapelry') AND u.steps > 0 THEN a.place_id END) AS township_id,
               max(u.steps) AS depth
        FROM up u JOIN place a ON a.place_id = u.anc_id
        GROUP BY u.place_id
    """)
    # One point per place: its own, preferring the sources in this order, else the nearest located
    # ancestor's. The order puts the survey's own point first, then KEPN (the same institute's
    # later work), then the two general gazetteers. All candidates stay in `geo`.
    con.execute("""
        CREATE TABLE place_point AS
        WITH own AS (
            SELECT place_id, lon, lat, source,
                   row_number() OVER (PARTITION BY place_id ORDER BY
                       CASE source WHEN 'epns' THEN 0 WHEN 'kepn' THEN 1 WHEN 'geonames' THEN 2 ELSE 3 END) AS rn
            FROM geo WHERE lon IS NOT NULL AND lat IS NOT NULL),
        own1 AS (SELECT place_id, lon, lat, source FROM own WHERE rn = 1),
        chain AS (
            WITH RECURSIVE up AS (
                SELECT place_id, place_id AS anc_id, 0 AS steps FROM place
                UNION ALL
                SELECT up.place_id, p.parent_id, up.steps + 1
                FROM up JOIN place p ON p.place_id = up.anc_id
                WHERE p.parent_id IS NOT NULL AND up.steps < 12)
            SELECT * FROM up),
        inherited AS (
            SELECT c.place_id, o.lon, o.lat, o.source, c.anc_id, c.steps,
                   row_number() OVER (PARTITION BY c.place_id ORDER BY c.steps) AS rn
            FROM chain c JOIN own1 o ON o.place_id = c.anc_id)
        SELECT i.place_id, i.lon, i.lat, i.source,
               CASE WHEN i.steps = 0 THEN NULL ELSE i.anc_id END AS inherited_from,
               i.steps AS inherited_steps
        FROM inherited i WHERE i.rn = 1
    """)
    con.execute("CREATE TABLE build_info AS SELECT now() AS built_at, ? AS source_dir, ? AS n_place, ? AS n_attestation",
                [str(SRC), counts["place"], counts["attestation"]])
    stats = {k: con.execute(f"SELECT count(*) FROM {k}").fetchone()[0]
             for k in [*SCHEMAS, "source", "place_tree", "place_point"]}
    con.close()
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=None, help="parse only the first N volumes (smoke test)")
    ap.add_argument("--no-expect", action="store_true", help="skip the fixed expected totals (other source files)")
    args = ap.parse_args()

    files = sorted(SRC.glob("vol*.xml"))
    if not files:
        sys.exit(f"no volumes in {SRC}; run process/fetch_data.sh first")
    if args.limit:
        files = files[: args.limit]
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.parquet"):
        old.unlink()

    t0 = time.time()
    t = Tables()
    next_id = 1
    for i, f in enumerate(files, 1):
        next_id = parse_volume(f, t, next_id)
        print(f"[{i:2d}/{len(files)}] {f.name:18s} places so far {t.written['place']:>8,}  "
              f"attestations {t.written['attestation']:>8,}  {time.time() - t0:5.0f}s", flush=True)
    t.close()
    counts = dict(t.written)
    print("parsed:", {k: f"{v:,}" for k, v in counts.items()})
    if t.unknown_children:
        print("WARNING: unhandled attestation/extension children (counts):", t.unknown_children)
    if t.bad_coords:
        print(f"NOTE: {len(t.bad_coords)} geo element(s) with an unparseable coordinate, nulled and kept raw:", t.bad_coords[:5])

    # Check 1: the regex instrument must agree with the parser on every tag it can count.
    rx = regex_counts(files)
    problems = []
    if rx["place"] != counts["place"]:
        problems.append(f"places: parser {counts['place']:,} vs regex {rx['place']:,}")
    if rx["place"] + rx["variant"] != counts["name"]:
        problems.append(f"names: parser {counts['name']:,} vs regex {rx['place'] + rx['variant']:,}")
    if rx["attestation"] != counts["attestation"]:
        problems.append(f"attestations: parser {counts['attestation']:,} vs regex {rx['attestation']:,}")
    if rx["date"] != counts["attestation_date"]:
        problems.append(f"dates: parser {counts['attestation_date']:,} vs regex {rx['date']:,}")
    if rx["geo"] != counts["geo"]:
        problems.append(f"geo: parser {counts['geo']:,} vs regex {rx['geo']:,}")
    if rx["searchterm"] != counts["searchterm"]:
        problems.append(f"searchterms: parser {counts['searchterm']:,} vs regex {rx['searchterm']:,}")
    # Check 2: the fixed totals from the independent profile (full corpus only).
    if not args.no_expect and not args.limit:
        for k, v in EXPECT.items():
            if counts[k] != v:
                problems.append(f"{k}: parsed {counts[k]:,} but the 26 Sep 2026 profile found {v:,}")
    if problems:
        print("COUNT CHECK FAILED:\n  " + "\n  ".join(problems))
        sys.exit(2)
    print("count checks passed (regex instrument" + ("" if args.limit or args.no_expect else " + fixed profile") + ")")

    stats = build_duckdb(counts)
    print("duckdb tables:", {k: f"{v:,}" for k, v in stats.items()})
    print(f"wrote {DB} and {OUT}/ in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Export the DuckDB build to the static files the Pages site reads (docs/data/).

    .venv/bin/python process/export_site.py            # data/deep.duckdb -> docs/data/

WHAT IS PRODUCED, and why it is shaped this way. The site is static: no server, no database,
"fetch only what you need", with IndexedDB caching what has been fetched. So the export is
split by how the page uses it:

  core.json            every place that is NOT a field name (~161k of 539k), columnar. Loaded at
                       boot: it is the map (own-coordinate places), the browse tree and the
                       breadcrumbs. Field names are 70% of records and are never plotted, so they
                       are kept out of the boot payload.
  county/<cc>.json     everything about every place in one county, field names included:
                       variants, attestations with their dates and apparatus, search terms,
                       coordinates from each gazetteer, notes. Fetched when a place or township
                       is opened, then cached.
  names/keys.json      the distinct name keys (lower-cased, bracket-free spellings), ordered so
                       that the keys the phonetic index covers come first. Text search scans
                       these; the Symphonym matrix is row-aligned with the first `n_embedded`.
  names/NN.json        every name form (headword, variant, search term) as a row pointing at its
                       key and its place, sorted by key so a key's rows are a contiguous range.
  manifest.json        sizes and sha256 of all of the above. The page fetches it no-cache and
                       uses the digests as IndexedDB cache keys, so a rebuild invalidates exactly
                       the files that changed and nothing else.

Global place ids (gid) are dense integers assigned here, by county then document order. They
appear in every file, and the manifest records each county's gid range, so a gid alone says
which county file to fetch. The original DEEP identifier is reconstructible from the columns
(epns-deep-<county>-<code>-<type>-<seq>) and is shown in the UI as the citable id.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

import duckdb

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
import sources as SRC  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "deep.duckdb"
OUT = ROOT / "docs" / "data"
NAME_SHARD_ROWS = 320_000

# County names come from the county records themselves (type = 'county'); codes 96-99 are the
# split volumes (Cambridgeshire / Ely, Bedfordshire / Huntingdonshire).


def dump(path: Path, obj) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path.write_bytes(data)
    return {"file": path.relative_to(OUT).as_posix(), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


_PUNCT = re.compile(r"[()\[\]{}.,;:!?\"“”‘’*+/\\|<>=_~`^#%&@]")
_WS = re.compile(r"\s+")


def uri_num(uri: str | None):
    """The county-wide serial in a DEEP URI: .../placename/02/000003 -> 3."""
    m = re.search(r"/(\d+)/(\d+)$", uri or "")
    return int(m.group(2)) if m else None


def name_key(text: str) -> str:
    """The key a name form is grouped and embedded under: NFC, brackets and punctuation removed
    (so 'Bunstow(e)' and 'Bunstowe' share a key), hyphens to spaces, lower-cased, whitespace
    collapsed. Letters are kept as they are, including æ þ ð and macrons: those are phonetic
    content for the embedding. The browser applies the same function to a query before the
    phonetic step (docs/js/search.worker.js `nameKey`); keep the two in step."""
    t = unicodedata.normalize("NFC", text or "")
    t = _PUNCT.sub("", t).replace("-", " ").replace("'", "")
    return _WS.sub(" ", t).strip().lower()


def main():
    t0 = time.time()
    con = duckdb.connect(str(DB), read_only=True)
    OUT.mkdir(parents=True, exist_ok=True)
    # Only this script's own outputs are removed. plato-sample.json (export_plato.py --sample) and
    # symphonym/ (build_symphonym_index.py) are other scripts' products; deleting them here once
    # silently broke phonetic search on the live site, because the matrix survived and its manifest
    # did not.
    for old in OUT.rglob("*.json"):
        if old.name == "plato-sample.json" or "symphonym" in old.parts:
            continue
        old.unlink()

    # ── gid assignment: county code, then the order the file listed them (seq within type is not
    #    document order, so use the rowid the loader wrote them in, which is document order).
    places = con.execute("""
        SELECT rowid AS r, place_id, county_code, type_code, type, seq, title, auth_type, parent_id, parent_title,
               content_source, created, n_variants, n_attestations, n_geo, title_uri, volume
        FROM place ORDER BY county_code, rowid""").fetchall()
    gid_of = {p[1]: i for i, p in enumerate(places)}
    n = len(places)
    print(f"{n:,} places, gids assigned in {time.time() - t0:.0f}s")

    tree = {r[0]: r[1:] for r in con.execute("SELECT place_id, county_id, hundred_id, parish_id, township_id FROM place_tree").fetchall()}
    point = {r[0]: r[1:] for r in con.execute("SELECT place_id, lon, lat, source, inherited_from FROM place_point").fetchall()}

    county_name = {r[0]: r[1] for r in con.execute("SELECT county_code, title FROM place WHERE type='county'").fetchall()}
    # children counts, split field-name / other
    kids = defaultdict(lambda: [0, 0])
    for pid, parent, typ in ((p[1], p[8], p[4]) for p in places):
        if parent in gid_of:
            kids[parent][0 if typ == "fn" else 1] += 1

    # ── core.json
    types = sorted({p[4] for p in places})
    type_ix = {t: i for i, t in enumerate(types)}
    codes = sorted(county_name)
    county_ix = {c: i for i, c in enumerate(codes)}
    core = {k: [] for k in ["gid", "title", "type", "county", "parent", "lon", "lat", "geo", "nVar", "nAtt", "nFn", "nKids", "code", "seq", "u", "hundred", "parish", "township"]}
    for i, p in enumerate(places):
        r, pid, cc, code, typ, seq, title, at, parent, ptitle, csrc, created, nv, na, ng, turi, volf = p
        if typ == "fn":
            continue
        pt = point.get(pid)
        tr = tree.get(pid, (None, None, None, None))
        core["gid"].append(i)
        core["title"].append(title)
        core["type"].append(type_ix[typ])
        core["county"].append(county_ix.get(cc, -1))
        core["parent"].append(gid_of.get(parent, -1))
        core["lon"].append(round(pt[0], 5) if pt else None)
        core["lat"].append(round(pt[1], 5) if pt else None)
        core["geo"].append(0 if not pt else (1 if pt[3] is None else 2))
        core["nVar"].append(nv)
        core["nAtt"].append(na)
        core["nFn"].append(kids[pid][0])
        core["nKids"].append(kids[pid][1])
        core["code"].append(code)
        core["seq"].append(seq)
        core["u"].append(uri_num(turi))          # DEEP's county-wide serial, from the record's own URI; what a w3id carries
        core["hundred"].append(gid_of.get(tr[1], -1))
        core["parish"].append(gid_of.get(tr[2], -1))
        core["township"].append(gid_of.get(tr[3], -1))
    core["types"] = types
    volume_of = {p[2]: p[16] for p in places if p[4] == "county"}     # county code -> source file, e.g. vol44mads.xml
    core["counties"] = [{"code": c, "name": county_name[c], "vol": int(re.search(r"vol(\d+)", volume_of.get(c, "vol0")).group(1)),
                         "gid": gid_of.get(next(p[1] for p in places if p[2] == c and p[4] == "county"), -1)} for c in codes]
    core_info = dump(OUT / "core.json", core)
    print(f"core.json: {len(core['gid']):,} places, {core_info['bytes'] / 1e6:.1f} MB")

    # ── county shards
    def rows_by_place(sql):
        d = defaultdict(list)
        for row in con.execute(sql).fetchall():
            d[row[0]].append(row[1:])
        return d

    variants = rows_by_place("SELECT place_id, name_id, toponym, uri FROM name WHERE kind='variant' ORDER BY rowid")
    # the authority heading, which DEEP gave the record's own URI; it joins a record's spellings only where a
    # citation or search term names it (27,447 citations do), or the page and formats.js would lose them
    authority = {pid: (nid, text, uri) for pid, nid, text, uri in
                 con.execute("SELECT place_id, name_id, toponym, uri FROM name WHERE kind='authority'").fetchall()}
    dates = defaultdict(list)
    for aid, sub, b, e, txt in con.execute("SELECT attestation_id, subtype, begin, \"end\", text FROM attestation_date ORDER BY attestation_id, seq").fetchall():
        dates[aid].append([sub, b, e, txt])
    atts = rows_by_place("""SELECT place_id, attestation_id, pos, parent_attestation_id, variant_ids, source_id, source_text, source_style,
                            source_underspec, copydate_text, copydate_begin, copydate_end, page, item, foliono, ms, pername,
                            entryno, appendixno, noteno, number, times FROM attestation ORDER BY place_id, pos""")
    passim = rows_by_place("SELECT place_id, pos, text FROM passim ORDER BY rowid")
    geo = rows_by_place("SELECT place_id, source, lon, lat, easting, northing, coalesce(gazref, kepnref, kepnexref), lon_raw, lat_raw FROM geo ORDER BY rowid")
    sterms = rows_by_place("SELECT place_id, variant_id, term FROM searchterm ORDER BY rowid")
    notes = rows_by_place("SELECT place_id, text FROM note ORDER BY rowid")
    print(f"detail tables loaded in {time.time() - t0:.0f}s")

    def short_vid(name_id: str) -> str:
        # epns-deep-02-hu-name-w22 -> w22 ; an authority heading's id shortens the same way (06/001111: 000318)
        return name_id.rsplit("-", 1)[-1] if name_id else ""

    EXTRA = ["page", "item", "folio", "ms", "pername", "entry", "appendix", "note", "number", "times"]
    source_reg = SRC.registry(con)
    county_files = []
    by_county = defaultdict(list)
    for i, p in enumerate(places):
        by_county[p[2]].append((i, p))
    for cc in sorted(by_county):
        recs = []
        srcs = {}      # the county's sources, keyed as formats.js looks them up: 'n:'+text (national) or sid / 'x-'+slug(text)
        gids = [i for i, _ in by_county[cc]]
        for i, p in by_county[cc]:
            r, pid, _, code, typ, seq, title, at, parent, ptitle, csrc, created, nv, na, ng, turi, volf = p
            rec = {"g": i, "t": title, "ty": type_ix[typ], "p": gid_of.get(parent, -1), "code": code, "seq": seq, "u": uri_num(turi)}
            if at and at != typ:
                rec["at"] = at
            if csrc:
                rec["cs"] = csrc
            if created:
                rec["cr"] = created
            if pid in variants:
                rec["v"] = [[short_vid(nid), text, uri_num(uri)] for nid, text, uri in variants[pid]]
            if pid in atts:
                pos_of = {}
                out_atts = []
                for (aid, pos, pa, vids, sid, stext, style, under, ctext, cb, ce, *extra) in atts[pid]:
                    pos_of[aid] = pos
                    a = {"p": pos, "v": [short_vid(v) for v in (vids or [])], "s": [sid, stext, 1 if style == "italic" else 0]}
                    if stext or sid:
                        if SRC.is_national(stext):
                            d = SRC.national_description(stext); srcs["n:" + stext] = [d["@id"], d["title"], d["citation"]]
                        else:
                            k = sid if sid else "x-" + SRC.slug(stext)
                            d = source_reg.get((cc, k)) or SRC.class_description(cc, county_name.get(cc, cc), sid, stext, style)
                            srcs[k] = [d["@id"], d["title"], d["citation"]]
                    if pa is not None:
                        a["pa"] = pos_of.get(pa)
                    if under:
                        a["u"] = under
                    if aid in dates:
                        a["d"] = dates[aid]
                    if ctext or cb or ce:
                        a["c"] = [ctext, cb, ce]
                    x = {k: v for k, v in zip(EXTRA, extra) if v}
                    if x:
                        a["x"] = x
                    out_atts.append(a)
                rec["a"] = out_atts
            if pid in passim:
                rec["ps"] = [[pos, text] for pos, text in passim[pid]]
            if pid in geo:
                rec["geo"] = [[src, lon, lat, e, nn, ref, lraw, traw] for src, lon, lat, e, nn, ref, lraw, traw in geo[pid]]
            if pid in sterms:
                rec["st"] = [[short_vid(v) if v else None, term] for v, term in sterms[pid]]
            if pid in notes:
                rec["n"] = [t for (t,) in notes[pid]]
            if pid in authority and authority[pid][1]:
                nid, text, uri = authority[pid]
                sv = short_vid(nid)
                if any(sv in a["v"] for a in rec.get("a", [])) or any(v == sv for v, _ in rec.get("st", [])):
                    rec["v"] = [[sv, text, uri_num(uri)]] + rec.get("v", [])
            recs.append(rec)
        volume = by_county[cc][0][1][16]
        info = dump(OUT / "county" / f"{cc}.json", {"code": cc, "name": county_name.get(cc), "volume": volume, "gidFrom": gids[0], "gidTo": gids[-1], "sources": srcs, "places": recs})
        info.update({"code": cc, "name": county_name.get(cc), "gidFrom": gids[0], "gidTo": gids[-1], "n": len(recs)})
        county_files.append(info)
    print(f"{len(county_files)} county files, {sum(f['bytes'] for f in county_files) / 1e6:.1f} MB in {time.time() - t0:.0f}s")

    # ── name index. kind: 0 headword, 1 variant, 2 search term. Every row is text-searchable; the
    #    `embed` flag decides which keys the phonetic matrix covers (see the comment below).
    rows = []  # (key, kind, gid, pgid, text, embed?)
    parent_gid = {i: gid_of.get(p[8], -1) for i, p in enumerate(places)}
    # Headwords of parishes, townships, hundreds and the like are embedded (a scribe's Snotingaham
    # should find Nottingham); the 111k minor-name and 378k field-name headwords are not, being
    # modern descriptive names whose historical spellings, where any exist, are embedded as variants.
    for i, p in enumerate(places):
        rows.append((name_key(p[6]), 0, i, parent_gid[i], p[6], p[4] not in ("fn", "mappedname")))
    for pid, vs in variants.items():
        g = gid_of[pid]
        for nid, text, uri in vs:
            rows.append((name_key(text), 1, g, parent_gid[g], text, True))
    for pid, ts in sterms.items():
        g = gid_of[pid]
        for v, term in ts:
            rows.append((name_key(term), 2, g, parent_gid[g], term, True))
    # Derived keys, kind 3: search aids pointing at the same record, so "bunsty" meets "Bunsty Hundred"
    # directly. Modelled on whg3's derivedNameVariants() (place#198-#205): the head minus a generic tail
    # (never the tail alone: a key "hundred" would sit beside every hundred in England), both sides of
    # "X or Y" / "X alias Y" (each capped at four words), and a comma inversion for a tail of two words
    # or fewer. Derived from headwords and spellings only, and embedded like them.
    # THE GENERIC-TAIL LIST IS DELIBERATELY ENGLISH-ONLY. It is a judgement about English toponymy
    # as the EPNS survey records it; Welsh, Scots or Cornish material would need its own list
    # (e.g. llan-, aber-, -ton is not a tail there), and this one was not meant to cover them.
    GENERIC = {"hundred", "wapentake", "liberty", "hundreds", "parish", "township", "farm", "farms", "house", "hall", "road",
               "street", "lane", "bridge", "hill", "wood", "field", "fields", "green", "common", "moor", "park", "mill", "cottage",
               "cottages", "lodge", "court", "grange", "end", "close", "lane", "drive", "avenue", "row", "place", "manor"}
    derived = []
    for key, kind, g, pg, text, emb in rows:
        if kind not in (0, 1) or not emb:
            continue
        words = key.split(" ")
        heads = set()
        if len(words) >= 2 and words[-1] in GENERIC and len(words) - 1 >= 1:
            heads.add(" ".join(words[:-1]))
        for sep in (" or ", " alias "):
            if sep in key:
                for side in key.split(sep):
                    side = side.strip()
                    if 1 <= len(side.split()) <= 4:
                        heads.add(side)
        if "," in text:
            parts = [x.strip() for x in text.split(",", 1)]
            if len(parts) == 2 and len(parts[1].split()) <= 2 and parts[1]:
                heads.add(name_key(parts[1] + " " + parts[0]))
        for h in heads:
            if h and h != key and len(h) > 1:
                derived.append((h, 3, g, pg, text, True))
    rows.extend(derived)
    rows = [r for r in rows if r[0]]
    embed_keys = sorted({r[0] for r in rows if r[5]})
    other_keys = sorted({r[0] for r in rows if not r[5]} - set(embed_keys))
    keys = embed_keys + other_keys
    key_ix = {k: i for i, k in enumerate(keys)}
    rows.sort(key=lambda r: (key_ix[r[0]], r[1], r[2]))
    keys_info = dump(OUT / "names" / "keys.json", keys)
    shards = []
    for s, start in enumerate(range(0, len(rows), NAME_SHARD_ROWS)):
        part = rows[start:start + NAME_SHARD_ROWS]
        obj = {"k": [key_ix[r[0]] for r in part], "kind": [r[1] for r in part], "gid": [r[2] for r in part],
               "pgid": [r[3] for r in part], "text": [r[4] for r in part]}
        info = dump(OUT / "names" / f"{s:02d}.json", obj)
        info.update({"rows": len(part), "kFrom": obj["k"][0], "kTo": obj["k"][-1]})
        shards.append(info)
    print(f"names: {len(rows):,} rows ({len(derived):,} derived), {len(keys):,} keys ({len(embed_keys):,} to embed), "
          f"{len(shards)} shards, {sum(s['bytes'] for s in shards) / 1e6:.1f} MB + keys {keys_info['bytes'] / 1e6:.1f} MB")

    counts = {k: con.execute(f"SELECT count(*) FROM {k}").fetchone()[0]
              for k in ["place", "name", "attestation", "attestation_date", "geo", "searchterm", "source"]}
    counts["place_located_own"] = con.execute("SELECT count(*) FROM place_point WHERE inherited_from IS NULL").fetchone()[0]
    counts["place_fn"] = con.execute("SELECT count(*) FROM place WHERE type='fn'").fetchone()[0]
    counts["date_min"], counts["date_max"] = con.execute(
        "SELECT min(begin), max(\"end\") FROM attestation_date WHERE begin > 400 AND \"end\" < 2000").fetchone()
    manifest = {
        "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "http://mads.digitalresources.jisc.ac.uk/mads2017/",
        "counts": counts, "nPlaces": n,
        "core": core_info, "counties": county_files,
        "names": {"keys": keys_info, "nKeys": len(keys), "nEmbedded": len(embed_keys), "shards": shards},
    }
    manifest["version"] = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()[:16]
    dump(OUT / "manifest.json", manifest)
    print(f"manifest version {manifest['version']}; total {sum(f.stat().st_size for f in OUT.rglob('*.json')) / 1e6:.1f} MB in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()

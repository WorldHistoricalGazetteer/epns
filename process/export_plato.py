#!/usr/bin/env python3
"""Export the DEEP corpus as PLATO (place-centric JSON) and as Linked Places Format, stamped with
the PLATO commit the output was validated against.

    .venv/bin/python process/export_plato.py --county 02            # one county, validated, to data/export/
    .venv/bin/python process/export_plato.py                        # all 66 counties + whole-corpus files
    .venv/bin/python process/export_plato.py --verify               # re-check every file's stamp against the manifest

WHY THE STAMP IS A COMMIT AND NOT A VERSION. PLATO's main has moved past its v0.3.0 tag: the
Citation class, source dating, occurrence and form status that this export exercises exist on
main and in no release, while owl:versionInfo still says "0.3.0". A file claiming "PLATO 0.3.0"
would therefore be a lie about which model it conforms to. So every output carries the full
commit sha of the schema it was VALIDATED against (the schema files are read out of that commit
with `git show`, not from the working tree), the manifest carries the same sha, and --verify
fails if any file and the manifest disagree. A regeneration against a later commit is then a
detectably different artefact rather than a silent replacement.

WHY PLACE-CENTRIC. PLATO's own schema says the place-centric profile is for "dataset ingestion,
legacy gazetteers and contributions that genuinely define new places", and the attestation-
centric one for evidence about SpatialEntities that already exist somewhere. No DEEP place
exists in any PLATO system, so every one of the 539,372 would have to go through the
attestation-centric profile's `newSpatialEntities` escape hatch, which the schema says to use
"sparingly". The place-centric document is the honest shape; each entity's attestations are the
same objects and can be re-emitted attestation-centric with an `about` in one line.

IDENTIFIERS. A SpatialEntity's @id is https://w3id.org/whg-epns/<county>/<serial>, and a Name's @id
the same pattern with the form's own serial: the <county>/<serial> pair is DEEP's, carried unchanged
from the URIs it published in 2013 (http://placenames.org.uk/id/placename/<county>/<serial>), which
no longer resolve because the domain has changed hands. DEEP numbered records and name forms in one
county-wide sequence, so a form's serial resolves to the record that carries it. The w3id namespace
is registered by the World Historical Gazetteer (decision of 26 Sep 2026); until the registration
is merged the URIs are declared but not yet resolvable, and the manifest says so. The original DEEP
URI and record id (epns-deep-...) are kept in the headword attestation's notes as provenance.
Attestations are addressed by fragment on the record's URI (#a<pos>, #headword, #s<n>, #g<n>).

WHAT IS EXERCISED, and where each DEEP element lands (the mapping the LPF export cannot make):
  attestation <date>          -> timespans[] with the editors' bracket as start/end earliest/latest
  <source>                    -> citations[].source (inline Source; title = abbreviation)
  <page>/<item>/<foliono>/... -> citations[].locator
  source@underspec ib/id      -> citations[].attributionStatus = plato#AttributionInferred
  <copydate> + <ms>           -> a witness Source with timespan, derivedFrom the work
  <times> "(3 X)"             -> occurrenceCount
  <pername> "(p)"             -> occurrenceContext = plato#InPersonalName
  <authority> headword        -> formStatus = plato#Headword (with types and the broader relation)
  <searchterm>                -> formStatus = plato#Normalised, cited to the DEEP project
  nested et-passim run        -> one attestation with a spanning timespan and one Citation per source
  <geo> per gazetteer         -> one geometry attestation each, cited to the gazetteer; GeoNames
                                 references also become identityRelations (closeMatch)
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sources as SRC  # noqa: E402  (source IRIs: one rule for every writer)

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "deep.duckdb"
OUT = ROOT / "data" / "export"
PLATO_REPO = Path("/home/stephen/PycharmProjects/place-attestation-ontology")
SITE = "https://worldhistoricalgazetteer.github.io/epns/"
W3ID = "https://w3id.org/whg-epns/"


def w3id_of(deep_uri: str | None) -> str | None:
    """DEEP's <county>/<serial> pair, re-homed under the w3id namespace."""
    m = re.search(r"/(\d+)/(\d+)$", deep_uri or "")
    return f"{W3ID}{m.group(1)}/{int(m.group(2)):06d}" if m else None
LICENCE = "https://creativecommons.org/licenses/by-nc/4.0/"
LICENCE_TEXT = ("Digitisation of English Placenames MADS data is licensed to Jisc by the English Place Names Society and "
                "released under a Creative Commons Attribution-NonCommercial 4.0 International License.")
PLATO = "https://w3id.org/plato#"
AAT = "http://vocab.getty.edu/aat/"
GEONAMES = "https://sws.geonames.org/{}/"

# DEEP type word -> (AAT concept, AAT label, LPF fclasses). AAT ids are from the LPF repository's
# feature-types-AAT_20230609.tsv (WHG's 176-concept list); nothing here is guessed. Field-names,
# hundreds and the rest are typed with the nearest concept on that list and keep their DEEP type
# word as sourceLabel, which is what the type is really telling you.
TYPES = {
    "county": ("300000771", "county", ["A"]), "province": ("300000774", "province", ["A"]),
    "subcounty": ("300232418", "administrative division", ["A"]), "dbhundred": ("300232418", "administrative division", ["A"]),
    "halfhundred": ("300232418", "administrative division", ["A"]), "liberty": ("300232418", "administrative division", ["A"]),
    "abovesubcounty": ("300232418", "administrative division", ["A"]), "belowsubcounty": ("300232418", "administrative division", ["A"]),
    "localdistrict": ("300232418", "administrative division", ["A"]),
    "parish": ("300232418", "administrative division", ["A", "P"]), "subparish": ("300232418", "administrative division", ["A", "P"]),
    "chapelry": ("300232418", "administrative division", ["A", "P"]), "belowparish": ("300232418", "administrative division", ["A", "P"]),
    "borough": ("300008375", "town", ["P"]), "countytown": ("300008375", "town", ["P"]),
    "mappedname": ("300008347", "inhabited place", ["P"]), "fn": ("300182722", "region (geographic)", ["L"]),
    "forest": ("300182722", "region (geographic)", ["L"]), "feature": ("300182722", "region (geographic)", ["L"]),
}
TYPE_WORD = {"subcounty": "hundred / wapentake", "dbhundred": "Domesday hundred", "halfhundred": "half-hundred", "subparish": "township",
             "belowparish": "sub-parish", "abovesubcounty": "division", "belowsubcounty": "sub-division of a hundred", "localdistrict": "local district",
             "countytown": "county town", "mappedname": "minor name", "fn": "field-name"}
AUTH_WORD = {"histfn": "historical field-name", "modfn": "modern field-name", "histmappedname": "historical minor name"}
GAZ = {"geonames": {"@id": "https://w3id.org/whg-epns/source/gazetteer/geonames", "authorityType": "dataset", "title": "GeoNames", "uri": "https://www.geonames.org/"},
       "unlock": {"@id": "https://w3id.org/whg-epns/source/gazetteer/unlock", "authorityType": "dataset", "title": "Unlock (EDINA) gazetteer", "citation": "EDINA Unlock Places gazetteer, as matched by DEEP in 2013; the service closed in 2015"},
       "kepn": {"@id": "https://w3id.org/whg-epns/source/gazetteer/kepn", "authorityType": "dataset", "title": "Key to English Place-Names (KEPN)", "uri": "https://kepn.nottingham.ac.uk/"},
       "epns": {"@id": "https://w3id.org/whg-epns/source/gazetteer/epns", "authorityType": "dataset", "title": "English Place-Name Society survey (DEEP coordinates)"}}
DEEP_SOURCE = {"@id": "https://w3id.org/whg-epns/source/deep", "authorityType": "dataset", "title": "DEEP: Digital Exposure of English Place-names (2011–13)",
               "citation": "Normalised search forms generated by the DEEP project from the survey's spellings; not attested in any source",
               "uri": "http://mads.digitalresources.jisc.ac.uk/mads2017/"}


def y(n):
    """A year as PLATO's isoOrYear string: four digits, zero-padded, sign kept."""
    return f"-{abs(n):04d}" if n < 0 else f"{n:04d}"


def timespan(sub, b, e, text):
    if b is None or e is None:
        return None
    if b > e:
        b, e = e, b
    ts = {"label": text}
    if sub == "simple" and b == e:
        ts.update(startEarliest=y(b), startLatest=y(b), endEarliest=y(e), endLatest=y(e), startPrecision="year", endPrecision="year", edtfString=y(b))
    elif sub == "ante":
        ts.update(startEarliest=y(b), startLatest=y(e), endLatest=y(e), endEarliest=y(b))
    elif sub == "post":
        ts.update(startEarliest=y(b), startLatest=y(e), endEarliest=y(b), endLatest=y(e))
    else:
        ts.update(startEarliest=y(b), startLatest=y(e), endEarliest=y(b), endLatest=y(e))
        if sub == "century":
            ts.update(startPrecision="century", endPrecision="century", edtfString=f"{b // 100:02d}XX")
        elif sub == "circa":
            ts["edtfString"] = f"{y((b + e) // 2)}~"
        if sub is None and b != e:
            ts.update(startLatest=y(b), endEarliest=y(e))   # "1288-1633": a run from one year to another
    ts["precisionValue"] = e - b
    return ts


def git_show(sha, path):
    return subprocess.check_output(["git", "-C", str(PLATO_REPO), "show", f"{sha}:{path}"], text=True)


def load_validator(sha):
    """The schema at exactly `sha`, so the stamp names what validated the output."""
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    core = json.loads(git_show(sha, "schemas/plato.schema.json"))
    pc = json.loads(git_show(sha, "schemas/place-centric.schema.json"))
    reg = Registry().with_resources([
        (core["$id"], Resource.from_contents(core)),
        ("plato.schema.json", Resource.from_contents(core)),
        (pc.get("$id", "place-centric.schema.json"), Resource.from_contents(pc)),
    ])
    return Draft202012Validator(pc, registry=reg, format_checker=Draft202012Validator.FORMAT_CHECKER)


class Exporter:
    def __init__(self, sha, con, tag=None, version_info=None):
        self.sha = sha
        self.tag = tag
        self.version_info = version_info
        self.con = con
        self.county_name = dict(con.execute("SELECT county_code, title FROM place WHERE type='county'").fetchall())
        self.volume = dict(con.execute("SELECT county_code, any_value(volume) FROM place GROUP BY 1").fetchall())
        self.sources = SRC.registry(con)
        self.n_att = 0
        self.n_ent = 0

    def volume_source(self, cc):
        vol = re.search(r"vol(\d+)", self.volume.get(cc, "") or "").group(1)
        return {"@id": SRC.volume_iri(cc), "title": f"English Place-Name Society survey, volume {int(vol)}: {self.county_name.get(cc, cc)}",
                "citation": f"The survey volume as digitised by DEEP (file {self.volume.get(cc)}); the volume's own list of sources expands the abbreviations",
                "authorityType": "source"}

    def source_for(self, a, cc):
        """The Source a citation points at: the work, or a dated witness derivedFrom the work."""
        sid, text, style, under, ctext, cb, ce, ms = a["source_id"], a["source_text"], a["source_style"], a["source_underspec"], a["copydate_text"], a["copydate_begin"], a["copydate_end"], a["ms"]
        if not text and not sid:
            return None
        # national sources share one IRI across volumes; class sources are per county (process/sources.py)
        if SRC.is_national(text):
            work = SRC.national_description(text)
        else:
            work = self.sources.get((cc, sid if sid else "x-" + SRC.slug(text))) or SRC.class_description(cc, self.county_name.get(cc, cc), sid, text, style)
        if ctext or cb or ce or ms:
            witness = {"@id": SRC.witness_iri(work["@id"], ms, ctext), "title": f"{work['title']}{' (' + ms + ')' if ms else ''}, the copy read", "authorityType": "source", "derivedFrom": work}
            ts = timespan("circa" if (cb is not None and ce is not None and cb != ce) else "simple", cb, ce, ctext or (ms or ""))
            if ts:
                ts["label"] = SRC.copy_label(ctext) if ctext else f"copy ({ms})"
                witness["timespan"] = ts
            return witness
        return work

    def citation(self, a, cc):
        src = self.source_for(a, cc)
        if not src:
            return None
        c = {"source": src}
        loc = [("p. " + a["page"]) if a["page"] else None, a["item"], ("f. " + a["foliono"]) if a["foliono"] else None,
               ("entry " + a["entryno"]) if a["entryno"] else None, ("appendix " + a["appendixno"]) if a["appendixno"] else None,
               ("note " + a["noteno"]) if a["noteno"] else None, a["number"]]
        loc = [x for x in loc if x]
        if loc:
            c["locator"] = ", ".join(loc)
        if a["source_underspec"] in ("ib", "id"):
            c["attributionStatus"] = PLATO + "AttributionInferred"
        return c

    def entity(self, p, names, atts, dates, passim, terms, geos, notes, tree):
        pid, cc, code, ptype, seq, title, title_uri, auth_type, parent, ptitle, csrc, created, vol = p
        uri = w3id_of(title_uri)
        if not title:                                   # one record in the corpus has an empty headword element
            title = f"[headword text missing in source: {pid}]"
        A = []
        name_uri = {n[0]: (n[2], w3id_of(n[3])) for n in names if n[2]}   # name_id -> (toponym, w3id); a few variants have no text at all
        blank = {n[0] for n in names if not n[2]}
        # 1. the headword: types, hierarchy, provenance
        aat, aat_label, _ = TYPES.get(ptype, TYPES["feature"])
        hw = {"@id": f"{uri}#headword", "formStatus": PLATO + "Headword",
              "names": [{"toponym": title, "language": "en"}],   # DEEP's URI for the headword IS the entity's @id, so it is not repeated here
              "types": [{"identifier": AAT + aat, "label": aat_label, "sourceLabel": f"{AUTH_WORD.get(auth_type) or TYPE_WORD.get(ptype, ptype)} (DEEP type '{ptype}', code '{code}')"}],
              "citations": [{"source": self.volume_source(cc)}],
              "notes": f"DEEP record {pid}; digitised {created}; DEEP URI {title_uri} (no longer resolves)"}
        if parent and tree.get(parent):
            hw["relations"] = [{"relatesTo": tree[parent], "relationType": "http://vocab.getty.edu/ontology#broaderPartitive",
                                "relationLabel": f"within {ptitle} (the survey's arrangement; structural, not dated)"}]
        if created:
            hw["created"] = f"{created}T00:00:00Z"
        if notes:
            hw["notes"] += "; " + "; ".join(notes)
        A.append(hw)
        # 2. attestations of spellings, runs handled as one attestation with several citations
        by_id = {a["attestation_id"]: a for a in atts}
        children = defaultdict(list)
        for a in atts:
            if a["parent_attestation_id"] is not None:
                children[a["parent_attestation_id"]].append(a)
        for a in atts:
            kids = children.get(a["attestation_id"], [])
            is_wrapper = not dates.get(a["attestation_id"]) and not a["source_text"] and kids
            nm = [{"@id": name_uri[v][1], "toponym": name_uri[v][0]} for v in (a["variant_ids"] or []) if v in name_uri]
            if is_wrapper:
                ds = [d for k in kids for d in dates.get(k["attestation_id"], []) if d[1] is not None and d[2] is not None]
                run = {"@id": f"{uri}#a{a['pos']}", "names": nm or [{"@id": name_uri[v][1], "toponym": name_uri[v][0]} for k in kids for v in (k["variant_ids"] or []) if v in name_uri][:1],
                       "citations": [c for c in (self.citation(k, cc) for k in kids) if c],
                       "notes": (lambda t: f"a run: the volume's '{t}' between these citations" if t else "a run: the spelling recurs between these citations (the volume's wording was not captured)")(
                           "; ".join(t for k in kids for t in passim.get(k["pos"], []) if t))}
                if ds:
                    b, e = min(d[1] for d in ds), max(d[2] for d in ds)
                    run["timespans"] = [{"startEarliest": y(b), "startLatest": y(b), "endEarliest": y(e), "endLatest": y(e), "label": f"{b}–{e}, recurring"}]
                A.append(run)
                continue
            att = {"@id": f"{uri}#a{a['pos']}", "names": nm}
            ts = [t for t in (timespan(d[0], d[1], d[2], d[3]) for d in dates.get(a["attestation_id"], [])) if t]
            if ts:
                att["timespans"] = ts
            c = self.citation(a, cc)
            if c:
                att["citations"] = [c]
            if a["times"]:
                m = re.search(r"(\d+)", a["times"])
                if m:
                    att["occurrenceCount"] = int(m.group(1))
            if a["pername"]:
                att["occurrenceContext"] = PLATO + "InPersonalName"
            if a["parent_attestation_id"] is not None:
                att["notes"] = "member of a run (see the attestation that spans it)"
            if not nm:
                why = "the cited spelling has no text in the source" if any(v in blank for v in (a["variant_ids"] or [])) else "cites no surviving spelling id"
                att["notes"] = (att.get("notes", "") + "; " + why).strip("; ")
            A.append(att)
        # 3. normalised search forms
        for i, (vid, term) in enumerate(terms):
            if not term:                                   # an empty <searchterm/> (a handful in the corpus)
                continue
            st = {"@id": f"{uri}#s{i + 1}", "names": [{"toponym": term}], "formStatus": PLATO + "Normalised", "citations": [{"source": DEEP_SOURCE}]}
            if vid and vid in name_uri:
                st["notes"] = f"normalised from the spelling {name_uri[vid][0]}"
            A.append(st)
        # 4. coordinates, one attestation per gazetteer candidate
        idr = []
        for i, (src, lon, lat, e, n, gazref, kepnref, kepnexref, lraw, traw) in enumerate(geos):
            if lon is None or lat is None:
                A.append({"@id": f"{uri}#g{i + 1}", "citations": [{"source": GAZ.get(src, {"title": src})}],
                          "notes": f"coordinate from {src} unparseable in the source: long='{lraw}' lat='{traw}'"})
                continue
            g = {"geojson": {"type": "Point", "coordinates": [lon, lat]}, "reprPoint": [lon, lat], "sourceCrs": "EPSG:4326"}
            ga = {"@id": f"{uri}#g{i + 1}", "geometries": [g], "citations": [{"source": GAZ.get(src, {"title": src})}]}
            ref = gazref or kepnref or kepnexref
            if ref:
                ga["citations"][0]["locator"] = f"gazetteer record {ref}"
            if e is not None and n is not None:
                ga["notes"] = f"also given on the British National Grid (EPSG:27700): E {e} N {n}"
            A.append(ga)
            if gazref and gazref.startswith("geonames:"):
                idr.append({"subject": uri, "object": GEONAMES.format(gazref.split(":", 1)[1]), "identityType": "closeMatch",
                            "basis": f"DEEP geo element, source geonames, gazref {gazref}", "source": GAZ["geonames"], "assertedBy": "DEEP (2013)"})
        self.n_att += len(A)
        self.n_ent += 1
        return {"@id": uri, "label": title, "attestations": A}, idr

    def gazetteer(self, cc=None):
        scope = f"county volume {self.county_name.get(cc, cc)}" if cc else "all 66 volumes"
        return {"@id": W3ID, "title": f"DEEP: the English Place-Name Society survey ({scope})",
                "description": (f"Place-centric PLATO serialisation of the DEEP MADS XML (Jisc, 2017 release; mirrored 2026-09-26), {scope}. "
                                + (f"Generated and schema-validated against PLATO {self.tag} (release {self.version_info}, commit {self.sha}). "
                                   if self.tag else
                                   f"Generated against PLATO commit {self.sha} (schemas validated at that commit; owl:versionInfo there reads {self.version_info} and is not the provenance). ")
                                + f"Identifiers are https://w3id.org/whg-epns/<county>/<serial>, carrying DEEP's own 2013 numbering (registration by the World Historical Gazetteer, pending merge at generation time). "
                                + f"Licence: {LICENCE_TEXT} This serialisation is an adaptation and carries the same terms. plato_commit={self.sha}"),
                "contributor": "Conversion: Stephen Gadd (github.com/WorldHistoricalGazetteer/epns). Data: English Place-Name Society, digitised by DEEP, published by Jisc.",
                "licence": LICENCE}

    def county_rows(self, cc):
        con = self.con
        places = con.execute("""SELECT place_id, county_code, type_code, type, seq, title, title_uri, auth_type, parent_id, parent_title, content_source, created, volume
                                FROM place WHERE county_code=? ORDER BY rowid""", [cc]).fetchall()
        tree = {r[0]: w3id_of(r[1]) for r in con.execute("SELECT place_id, title_uri FROM place WHERE county_code=?", [cc]).fetchall()}
        def grouped(sql):
            d = defaultdict(list)
            for row in con.execute(sql, [cc]).fetchall():
                d[row[0]].append(row[1:])
            return d
        names = grouped("SELECT n.place_id, n.name_id, n.kind, n.toponym, n.uri FROM name n JOIN place p USING (place_id) WHERE p.county_code=? ORDER BY n.rowid")
        cols = ["attestation_id", "pos", "parent_attestation_id", "variant_ids", "source_id", "source_text", "source_style", "source_underspec",
                "copydate_text", "copydate_begin", "copydate_end", "page", "item", "foliono", "ms", "pername", "entryno", "appendixno", "noteno", "number", "times"]
        atts = defaultdict(list)
        for row in con.execute(f"SELECT a.place_id, {', '.join('a.' + c for c in cols)} FROM attestation a JOIN place p USING (place_id) WHERE p.county_code=? ORDER BY a.pos", [cc]).fetchall():
            atts[row[0]].append(dict(zip(cols, row[1:])))
        dates = defaultdict(list)
        for aid, sub, b, e, text in con.execute("""SELECT d.attestation_id, d.subtype, d.begin, d."end", d.text FROM attestation_date d JOIN attestation a USING (attestation_id)
                                                   JOIN place p USING (place_id) WHERE p.county_code=? ORDER BY d.attestation_id, d.seq""", [cc]).fetchall():
            dates[aid].append((sub, b, e, text))
        passim = defaultdict(lambda: defaultdict(list))
        for pid, pos, text in con.execute("SELECT s.place_id, s.pos, s.text FROM passim s JOIN place p USING (place_id) WHERE p.county_code=?", [cc]).fetchall():
            passim[pid][pos].append(text)
        terms = grouped("SELECT s.place_id, s.variant_id, s.term FROM searchterm s JOIN place p USING (place_id) WHERE p.county_code=? ORDER BY s.rowid")
        geos = grouped("SELECT g.place_id, g.source, g.lon, g.lat, g.easting, g.northing, g.gazref, g.kepnref, g.kepnexref, g.lon_raw, g.lat_raw FROM geo g JOIN place p USING (place_id) WHERE p.county_code=? ORDER BY g.rowid")
        notes = grouped("SELECT n.place_id, n.text FROM note n JOIN place p USING (place_id) WHERE p.county_code=?")
        return places, names, atts, dates, passim, terms, geos, notes, tree

    def county_document(self, cc):
        places, names, atts, dates, passim, terms, geos, notes, tree = self.county_rows(cc)
        ents, idrs = [], []
        for p in places:
            ent, idr = self.entity(p, names.get(p[0], []), atts.get(p[0], []), dates, passim.get(p[0], {}), terms.get(p[0], []), geos.get(p[0], []),
                                   [t for (t,) in notes.get(p[0], [])], tree)
            ents.append(ent)
            idrs.extend(idr)
        doc = {"$schema": "https://w3id.org/plato/schemas/place-centric.schema.json", "profile": "place-centric",
               "gazetteer": self.gazetteer(cc), "spatialEntities": ents}
        if idrs:
            doc["identityRelations"] = idrs
        return doc

    # ── LPF ───────────────────────────────────────────────────────────────────────────────────
    def lpf_feature(self, ent, idrs_for):
        """The same entity as an LPF v1.3 Feature. Lossy by construction; the losses are listed in
        docs/downloads.html and marked inline by the site's record panel."""
        A = ent["attestations"]
        hw = A[0]
        names, years, geoms, links, relations, descs = [], [], [], [], [], []
        # headword first, cited to the volume
        names.append({"toponym": ent["label"], "lang": "en", "citations": [{"label": hw["citations"][0]["source"]["title"]}]})
        by_form = {}
        for a in A[1:]:
            if a.get("formStatus") == PLATO + "Normalised":
                continue                          # no LPF slot without conflating with attested spellings
            if a.get("geometries"):
                g = dict(a["geometries"][0]["geojson"])
                g["citations"] = [{"label": a["citations"][0]["source"]["title"] + (", " + a["citations"][0]["locator"] if a["citations"][0].get("locator") else "")}]
                geoms.append(g)
                continue
            if not a.get("names"):
                continue
            cits = []
            for c in a.get("citations", []):
                src = c["source"]
                lab = src["title"]
                if src.get("derivedFrom"):
                    lab = src["derivedFrom"]["title"]
                cits.append({"label": " ".join(t for t in [(a.get("timespans") or [{}])[0].get("label", ""), lab] if t)})
            yr = None
            for t in a.get("timespans", []):
                if t.get("startEarliest"):
                    yr = yr or int(t["startEarliest"])
                    years.append(int(t["startEarliest"]))
                if t.get("endLatest"):
                    years.append(int(t["endLatest"]))
            if yr is not None and cits:
                cits[0]["year"] = yr
            for n in a["names"]:
                key = n["toponym"]
                if key not in by_form:
                    by_form[key] = {"toponym": key, "citations": [], "_spans": []}
                    names.append(by_form[key])
                by_form[key]["citations"].extend(cits)
                for t in a.get("timespans", []):
                    by_form[key]["_spans"].append({"start": {"earliest": t.get("startEarliest"), "latest": t.get("startLatest")} if t.get("startLatest") != t.get("startEarliest") else {"in": t.get("startEarliest")},
                                                   **({"end": {"earliest": t.get("endEarliest"), "latest": t.get("endLatest")} if t.get("endLatest") != t.get("endEarliest") else {"in": t.get("endLatest")}} if t.get("endLatest") else {})})
        for item in names[1:]:                    # LPF: citations[] and when are PARALLEL arrays, which is loss 1 in discussions/53
            spans = item.pop("_spans", [])
            if spans:
                item["when"] = {"timespans": spans}
            if not item["citations"]:
                del item["citations"]
        for idr in idrs_for:
            links.append({"type": "closeMatch", "identifier": idr["object"]})
        for r in hw.get("relations", []):
            relations.append({"relationType": "gvp:broaderPartitive", "relationTo": r["relatesTo"], "label": r["relationLabel"].split(" (")[0]})
        t = hw["types"][0]
        fcl = TYPES.get(t["sourceLabel"].split("DEEP type '")[1].split("'")[0], TYPES["feature"])[2] if "DEEP type '" in t["sourceLabel"] else ["L"]
        feat = {"@id": ent["@id"], "type": "Feature", "properties": {"title": ent["label"], "ccodes": ["GB"], "fclasses": fcl},
                "names": names, "types": [{"identifier": t["identifier"], "label": t["label"], "sourceLabels": [{"label": t["sourceLabel"].split(" (DEEP")[0], "lang": "en"}]}],
                "geometry": None if not geoms else (geoms[0] if len(geoms) == 1 else {"type": "GeometryCollection", "geometries": geoms})}
        if years:
            feat["when"] = {"timespans": [{"start": {"in": y(min(years))}, "end": {"in": y(max(years))}}]}
        if links:
            feat["links"] = links
        if relations:
            feat["relations"] = relations
        if descs:
            feat["descriptions"] = descs
        return feat


def assemble(out: Path, sha: str, version_info: str, tag: str | None = None):
    """Whole-corpus files from the per-county ones. Every county file must carry the same plato_commit
    stamp as the one being assembled under, or the result would mix models; that is checked here."""
    con = duckdb.connect(str(DB), read_only=True)
    codes = sorted(dict(con.execute("SELECT county_code, title FROM place WHERE type='county'").fetchall()))
    names = dict(con.execute("SELECT county_code, title FROM place WHERE type='county'").fetchall())
    ex = Exporter(sha, con, tag, version_info)
    files, n_ent, n_att = [], 0, 0
    t0 = time.time()
    with gzip.open(out / "deep-plato.jsonl.gz", "wt", encoding="utf-8") as whole, gzip.open(out / "deep-lpf.geojsonl.gz", "wt", encoding="utf-8") as whole_lpf:
        whole.write(json.dumps({"$schema": "https://w3id.org/plato/schemas/place-centric.schema.json", "profile": "place-centric", "gazetteer": ex.gazetteer(),
                                "note": "JSON Lines: this header line, then one spatialEntity per line; identityRelations follow as lines with a 'subject' key. Reassemble into the place-centric document by collecting them.",
                                "plato_commit": sha}, ensure_ascii=False) + "\n")
        whole_lpf.write(json.dumps({"@context": "https://raw.githubusercontent.com/LinkedPasts/linked-places-format/main/linkedplaces-context-v1.1.jsonld", "type": "FeatureCollection",
                                    "note": f"GeoJSON Text Sequence: one LPF v1.3 Feature per following line. Lossy; see {SITE}downloads.html. plato_commit={sha} (the PLATO export this was derived from)",
                                    "license": LICENCE_TEXT}, ensure_ascii=False) + "\n")
        for cc in codes:
            p1, p2 = out / "plato" / f"deep-plato-{cc}.json.gz", out / "lpf" / f"deep-lpf-{cc}.geojson.gz"
            if not p1.exists() or not p2.exists():
                sys.exit(f"county {cc} has not been exported; run --county {cc} first")
            with gzip.open(p1, "rt", encoding="utf-8") as fh:
                doc = json.load(fh)
            m = STAMP_RE.search(doc["gazetteer"]["description"])
            if not m or m.group(1) != sha:
                sys.exit(f"county {cc} was exported against {m.group(1)[:12] if m else 'no stamp'}, not {sha[:12]}; re-export it before assembling")
            for ent in doc["spatialEntities"]:
                whole.write(json.dumps(ent, ensure_ascii=False) + "\n")
                n_att += len(ent["attestations"])
            n_ent += len(doc["spatialEntities"])
            for r in doc.get("identityRelations", []):
                whole.write(json.dumps(r, ensure_ascii=False) + "\n")
            with gzip.open(p2, "rt", encoding="utf-8") as fh:
                lp = json.load(fh)
            if f"plato_commit={sha}" not in lp.get("note", ""):
                sys.exit(f"LPF county {cc} carries a different stamp; re-export it")
            for f in lp["features"]:
                whole_lpf.write(json.dumps(f, ensure_ascii=False) + "\n")
            for p in (p1, p2):
                files.append({"file": p.relative_to(out).as_posix(), "bytes": p.stat().st_size, "sha256": sha256_file(p), "county": cc, "county_name": names.get(cc)})
            print(f"  {cc} {names.get(cc, ''):28s} assembled  {time.time() - t0:4.0f}s", flush=True)
    for p in (out / "deep-plato.jsonl.gz", out / "deep-lpf.geojsonl.gz"):
        files.append({"file": p.name, "bytes": p.stat().st_size, "sha256": sha256_file(p)})
    old = json.loads((out / "manifest.json").read_text()) if (out / "manifest.json").exists() else {}
    manifest = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "plato_commit": sha, "plato_tag": tag, "plato_versionInfo_at_commit": version_info,
                "identifiers": {"namespace": W3ID, "pattern": W3ID + "<county>/<serial>", "note": "DEEP's own 2013 numbering under a w3id namespace registered by WHG; resolvable once the perma-id/w3id.org registration is merged"},
                "plato_repo": "https://github.com/pelagios/place-attestation-ontology", "validated": True, "schema_errors": 0,
                "assembled_from_county_files": True, "previous_generated": old.get("generated"),
                "source": "http://mads.digitalresources.jisc.ac.uk/mads2017/ (files dated 2017; mirrored 2026-09-26)", "licence": LICENCE, "licence_text": LICENCE_TEXT,
                "entities": n_ent, "attestations": n_att, "files": files}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
    print(f"assembled {n_ent:,} entities, {n_att:,} attestations from {len(codes)} county files under plato_commit {sha[:12]} in {time.time() - t0:.0f}s")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


STAMP_RE = re.compile(r"plato_commit=([0-9a-f]{40})")


def verify(out: Path) -> int:
    man = json.loads((out / "manifest.json").read_text())
    want = man["plato_commit"]
    bad = 0
    for f in man["files"]:
        p = out / f["file"]
        if not p.exists():
            print(f"MISSING {f['file']}"); bad += 1; continue
        if sha256_file(p) != f["sha256"]:
            print(f"SHA256 MISMATCH {f['file']}"); bad += 1
        opener = gzip.open if p.suffix == ".gz" else open
        with opener(p, "rt", encoding="utf-8") as fh:
            head = fh.read(200_000)
        m = STAMP_RE.search(head)
        if not m:
            print(f"NO STAMP in {f['file']}"); bad += 1
        elif m.group(1) != want:
            print(f"STAMP MISMATCH {f['file']}: file {m.group(1)[:12]} manifest {want[:12]}"); bad += 1
    print(f"verify: {len(man['files'])} files, {bad} problem(s); plato_commit {want}")
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--county", action="append", help="export only these county codes")
    ap.add_argument("--plato-commit", default=None, help="commit of the PLATO repo to validate against (default: origin/main)")
    ap.add_argument("--no-validate", action="store_true")
    ap.add_argument("--verify", action="store_true", help="check every file's stamp and digest against the manifest, then exit")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--assemble", action="store_true",
                    help="rebuild the whole-corpus JSONL/GeoJSONL and the manifest from the per-county files already in --out (after re-exporting one county)")
    ap.add_argument("--sample", nargs="*", metavar="DEEP_ID",
                    help="write docs/data/plato-sample.json with the PLATO entity and LPF feature of these records (short ids like 02-hu-subcounty-000001)")
    args = ap.parse_args()
    out = Path(args.out)
    if args.verify:
        sys.exit(1 if verify(out) else 0)

    sha = args.plato_commit or subprocess.check_output(["git", "-C", str(PLATO_REPO), "rev-parse", "origin/main"], text=True).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        sys.exit(f"not a full commit sha: {sha}")
    version_info = re.search(r'owl:versionInfo\s+"([^"]+)"', git_show(sha, "ontology.ttl")).group(1)
    # A tag at exactly this commit whose name matches versionInfo makes the release citable by
    # version; otherwise the commit is the only honest provenance and the files say so.
    tags = subprocess.check_output(["git", "-C", str(PLATO_REPO), "tag", "--points-at", sha], text=True).split()
    tag = next((t for t in tags if t.lstrip("v") == version_info), None)
    validator = None if args.no_validate else load_validator(sha)
    con = duckdb.connect(str(DB), read_only=True)
    ex = Exporter(sha, con, tag, version_info)
    if args.assemble:
        assemble(out, sha, version_info, tag)
        return
    if args.sample is not None:
        ids = args.sample or ["02-hu-subcounty-000001", "02-a-parish-000001", "52-b-subparish-000031", "96-ct-countytown-000001", "28-e-fn-000001", "05-b-subparish-000001"]
        want = {"epns-deep-" + i: i for i in ids}
        sample = {"plato_commit": sha, "plato_tag": tag, "plato_versionInfo_at_commit": version_info, "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "records": {}}
        for cc in sorted({i.split("-")[0] for i in ids}):
            doc = ex.county_document(cc)
            if validator:
                errs = list(validator.iter_errors(doc))
                if errs:
                    sys.exit(f"sample county {cc} fails validation: {errs[0].message[:200]}")
            idr_by = defaultdict(list)
            for r in doc.get("identityRelations", []):
                idr_by[r["subject"]].append(r)
            for ent in doc["spatialEntities"]:
                m = re.search(r"DEEP record (epns-deep-[^;]+);", ent["attestations"][0]["notes"])
                if m and m.group(1) in want:
                    sample["records"][want[m.group(1)]] = {"plato": ent, "identityRelations": idr_by.get(ent["@id"], []), "lpf": ex.lpf_feature(ent, idr_by.get(ent["@id"], []))}
        missing = set(ids) - set(sample["records"])
        if missing:
            sys.exit(f"sample records not found: {missing}")
        p = ROOT / "docs" / "data" / "plato-sample.json"
        p.write_text(json.dumps(sample, ensure_ascii=False, indent=1))
        print(f"wrote {p} with {len(sample['records'])} records, plato_commit {sha[:12]}")
        return
    counties = args.county or sorted(ex.county_name)
    (out / "plato").mkdir(parents=True, exist_ok=True)
    (out / "lpf").mkdir(parents=True, exist_ok=True)
    files = []
    t0 = time.time()
    whole = None if args.county else gzip.open(out / "deep-plato.jsonl.gz", "wt", encoding="utf-8")
    whole_lpf = None if args.county else gzip.open(out / "deep-lpf.geojsonl.gz", "wt", encoding="utf-8")
    if whole:
        whole.write(json.dumps({"$schema": "https://w3id.org/plato/schemas/place-centric.schema.json", "profile": "place-centric",
                                "gazetteer": ex.gazetteer(), "note": "JSON Lines: this header line, then one spatialEntity per line; identityRelations follow as lines with a 'subject' key. Reassemble into the place-centric document by collecting them.",
                                "plato_commit": sha}, ensure_ascii=False) + "\n")
        whole_lpf.write(json.dumps({"@context": "https://raw.githubusercontent.com/LinkedPasts/linked-places-format/main/linkedplaces-context-v1.1.jsonld", "type": "FeatureCollection",
                                    "note": f"GeoJSON Text Sequence: one LPF v1.3 Feature per following line. Lossy; see {SITE}downloads.html. plato_commit={sha} (the PLATO export this was derived from)",
                                    "license": LICENCE_TEXT}, ensure_ascii=False) + "\n")
    n_val_err = 0
    for cc in counties:
        doc = ex.county_document(cc)
        if validator:
            errs = list(validator.iter_errors(doc))
            if errs:
                n_val_err += len(errs)
                for e in errs[:5]:
                    print(f"  SCHEMA {cc}: {'/'.join(map(str, e.absolute_path))[:120]}: {e.message[:160]}")
        idr_by = defaultdict(list)
        for r in doc.get("identityRelations", []):
            idr_by[r["subject"]].append(r)
        feats = [ex.lpf_feature(ent, idr_by.get(ent["@id"], [])) for ent in doc["spatialEntities"]]
        p1 = out / "plato" / f"deep-plato-{cc}.json.gz"
        with gzip.open(p1, "wt", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False)
        p2 = out / "lpf" / f"deep-lpf-{cc}.geojson.gz"
        with gzip.open(p2, "wt", encoding="utf-8") as fh:
            json.dump({"@context": "https://raw.githubusercontent.com/LinkedPasts/linked-places-format/main/linkedplaces-context-v1.1.jsonld", "type": "FeatureCollection",
                       "license": LICENCE_TEXT, "note": f"LPF v1.3, lossy; derived from the PLATO export; plato_commit={sha}", "features": feats}, fh, ensure_ascii=False)
        if whole:
            for ent in doc["spatialEntities"]:
                whole.write(json.dumps(ent, ensure_ascii=False) + "\n")
            for r in doc.get("identityRelations", []):
                whole.write(json.dumps(r, ensure_ascii=False) + "\n")
            for f in feats:
                whole_lpf.write(json.dumps(f, ensure_ascii=False) + "\n")
        for p in (p1, p2):
            files.append({"file": p.relative_to(out).as_posix(), "bytes": p.stat().st_size, "sha256": sha256_file(p), "county": cc, "county_name": ex.county_name.get(cc)})
        print(f"{cc} {ex.county_name.get(cc, ''):28s} {len(doc['spatialEntities']):>7,} entities  {sum(len(e['attestations']) for e in doc['spatialEntities']):>8,} attestations  "
              f"{p1.stat().st_size / 1e6:5.1f} MB gz  {time.time() - t0:5.0f}s", flush=True)
    if whole:
        whole.close(); whole_lpf.close()
        for p in (out / "deep-plato.jsonl.gz", out / "deep-lpf.geojsonl.gz"):
            files.append({"file": p.name, "bytes": p.stat().st_size, "sha256": sha256_file(p)})
    manifest = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "plato_commit": sha, "plato_tag": tag, "plato_versionInfo_at_commit": version_info,
                "identifiers": {"namespace": W3ID, "pattern": W3ID + "<county>/<serial>", "note": "DEEP's own 2013 numbering under a w3id namespace registered by WHG; resolvable once the perma-id/w3id.org registration is merged"},
                "plato_repo": "https://github.com/pelagios/place-attestation-ontology", "validated": not args.no_validate, "schema_errors": n_val_err,
                "source": "http://mads.digitalresources.jisc.ac.uk/mads2017/ (files dated 2017; mirrored 2026-09-26)", "licence": LICENCE, "licence_text": LICENCE_TEXT,
                "entities": ex.n_ent, "attestations": ex.n_att, "files": files}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
    print(f"\n{ex.n_ent:,} entities, {ex.n_att:,} attestations; schema errors {n_val_err}; plato_commit {sha[:12]} ({'tag ' + tag if tag else 'untagged'}, versionInfo {version_info}); {time.time() - t0:.0f}s")
    if n_val_err:
        sys.exit(2)


if __name__ == "__main__":
    main()

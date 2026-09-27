#!/usr/bin/env python3
"""The whole corpus as RDF: data/export/deep-plato.nt.gz (N-Triples, gzipped), from the validated PLATO files.

    .venv/bin/python process/export_rdf.py              # ~12 min on 12 processes; appends itself to data/export/manifest.json
    .venv/bin/python process/export_rdf.py --county 99  # one county, to stdout, for a look

WHERE THE TRIPLES COME FROM. Each county's place-centric PLATO document (data/export/plato/, the files
export_plato.py validated and stamped) is expanded with PLATO's own JSON-LD context, taken from the latest
commit of schemas/plato.context.jsonld on PLATO main, and turned into triples by pyld. Nothing is mapped
here: a key the context does not define does not become a triple. The inputs are checked against the export
manifest's sha256 first, so the triples cannot come from files other than the ones the manifest describes.

WHAT IS ADDED, AND WHY. The context's own $comment lists what a context cannot do and a triplifier must.
This one does three of those things, and one more,, each by rule from the ontology (ontology.ttl at the same PLATO commit), not by hand:

  rdf:type      For every triple, the subject is typed with the predicate's rdfs:domain and an IRI or
                blank-node object with its rdfs:range, where that is one named plato: class. This is the
                RDFS inference the $comment says a reasoner would make, materialised so that a store
                without a reasoner can still ask for every plato:Attestation.
  datatypes     A plain literal on a predicate whose declared range is an XSD datatype other than string
                gets that datatype (xsd:dateTime, xsd:anyURI) when its lexical form fits it. Timespan
                bounds, declared rdfs:Literal, are typed by shape: xsd:gYear for a year (DEEP's bounds
                are years, zero-padded: '0990'), xsd:date for a full date. Anything else stays plain.
  repr_point    The context yields an RDF list of two numbers; the ontology declares geo:wktLiteral, so
                it becomes "POINT(lon lat)"^^geo:wktLiteral.
  timespans     A copy witness's date is named <witness IRI>#timespan instead of a blank node, so the
                witness and its date are written once, not once per record that cites it.

Not done: name strings carry no language tag. DEEP's forms are Old English, Middle English, Anglo-Norman
and Latin spellings in one field, and 'en' on Bonestou would be false.

ONE GRAPH, NOT 66. Every county file names the same gazetteer node (https://w3id.org/whg-epns/), with a
county-specific title; merged, that node would carry 66 titles. The corpus-wide gazetteer header from
deep-plato.jsonl.gz replaces them, so it is described once. Triples about shared nodes (the gazetteer,
sources, volumes, and the types of outside IRIs such as GeoNames features) are written once. Blank nodes are relabelled per county (_:c52b123), since pyld numbers
each document from _:b0.
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
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "data" / "export"
PLATO_REPO = Path.home() / "PycharmProjects" / "place-attestation-ontology"
OUT_NAME = "deep-plato.nt.gz"
P = "https://w3id.org/plato#"
XSD = "http://www.w3.org/2001/XMLSchema#"
RDFTYPE = "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>"
WKT = "http://www.opengis.net/ont/geosparql#wktLiteral"
BOUNDS = {f"<{P}{k}>" for k in ("start_earliest", "start_latest", "end_earliest", "end_latest")}
SHARED = ("<https://w3id.org/whg-epns/source/", "<https://w3id.org/whg-epns/volume/")
GAZ_IRI = "<https://w3id.org/whg-epns/>"
LEX = {"dateTime": re.compile(r"^-?\d{4,}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)?$"),
       "anyURI": re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:\S+$"),
       "integer": re.compile(r"^[+-]?\d+$")}
PLAIN = re.compile(r'^(\S+) (<[^>]+>) "((?:[^"\\]|\\.)*)" \.$')      # a plain literal, no datatype, no language
G = {}                                                               # per-process state, filled by init()


def git(*a):
    return subprocess.check_output(["git", "-C", str(PLATO_REPO), *a], text=True)


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def schema(commit):
    """(context, domain, range, datatype range) from PLATO at `commit`: only single named plato: classes."""
    import rdflib
    from rdflib.namespace import RDFS
    ctx = json.loads(git("show", f"{commit}:schemas/plato.context.jsonld"))["@context"]
    g = rdflib.Graph().parse(data=git("show", f"{commit}:ontology.ttl"), format="turtle")
    dom, rng, dt = {}, {}, {}
    for p, o in g.subject_objects(RDFS.domain):
        if isinstance(o, rdflib.URIRef) and str(o).startswith(P):
            dom[f"<{p}>"] = f"<{o}>"
    for p, o in g.subject_objects(RDFS.range):
        if isinstance(o, rdflib.URIRef) and str(o).startswith(P):
            rng[f"<{p}>"] = f"<{o}>"
        elif isinstance(o, rdflib.URIRef) and str(o).startswith(XSD) and str(o)[len(XSD):] in LEX:
            dt[f"<{p}>"] = str(o)[len(XSD):]
    return ctx, dom, rng, dt


def init(ctx, dom, rng, dt, gaz):
    G.update(ctx=ctx, dom=dom, rng=rng, dt=dt, gaz=gaz)


def wkt(node):
    """Expanded JSON-LD: repr_point's @list of two numbers -> one geo:wktLiteral, in place, everywhere."""
    if isinstance(node, list):
        for x in node:
            wkt(x)
    elif isinstance(node, dict):
        # a witness is shared by every record that cites it; its timespan, a blank node in the JSON, would be
        # minted again by each one. Named <witness>#timespan it is one node, written once like the witness.
        ts = P + "source_timespan"
        if "/witness/" in node.get("@id", "") and ts in node:
            for t in node[ts]:
                if isinstance(t, dict) and "@id" not in t:
                    t["@id"] = node["@id"] + "#timespan"
        k = P + "repr_point"
        if k in node:
            out = []
            for v in node[k]:
                xs = [i.get("@value") for i in v.get("@list", [])] if isinstance(v, dict) else []
                out.append({"@value": f"POINT({xs[0]} {xs[1]})", "@type": WKT} if len(xs) == 2 else v)
            node[k] = out
        for v in node.values():
            if isinstance(v, (list, dict)):
                wkt(v)


def county(cc):
    doc = json.load(gzip.open(EXP / "plato" / f"deep-plato-{cc}.json.gz"))
    doc["gazetteer"] = G["gaz"]
    return cc, triples(doc, f"c{cc}")


def triples(doc, label=None):
    """One PLATO document -> N-Triples lines: expand with the context, name witness dates, WKT points, then type
    and datatype by the ontology. docs/js/rdf.js is a port of exactly this, checked by the harness."""
    from pyld import jsonld
    doc = dict(doc, **{"@context": G["ctx"]})
    exp = jsonld.expand(doc)
    wkt(exp)
    lines = jsonld.to_rdf(exp, {"format": "application/n-quads"}).splitlines()
    dom, rng, dt = G["dom"], G["rng"], G["dt"]
    out, types = [], set()
    for ln in lines:
        if label:
            ln = re.sub(r"_:b(\d+)", rf"_:{label}b\1", ln)
        s, p, rest = ln.split(" ", 2)
        m = PLAIN.match(ln)
        if m:
            lex = m.group(3)
            if p in BOUNDS:
                t = "gYear" if re.fullmatch(r"-?\d{4,}", lex) else "date" if re.fullmatch(r"-?\d{4,}-\d\d-\d\d", lex) else None
            else:
                t = dt.get(p) if dt.get(p) and LEX[dt[p]].match(lex) else None
            if t:
                ln = f'{s} {p} "{lex}"^^<{XSD}{t}> .'
        out.append(ln)
        if p in dom:
            types.add(f"{s} {RDFTYPE} {dom[p]} .")
        o = rest[:-2]
        if p in rng and (o.startswith("<") or o.startswith("_:")):
            types.add(f"{o} {RDFTYPE} {rng[p]} .")
    return out + sorted(types - set(out))       # pyld already types sources from authorityType


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--county", help="one county, to stdout")
    ap.add_argument("--procs", type=int, default=12)
    ap.add_argument("--site", action="store_true",
                    help="write docs/data/rdf/ (context, rules, canonical fixture graphs) for the per-record RDF view, "
                         "pinned to the context commit the published deep-plato.nt.gz was built with; touches no export")
    args = ap.parse_args()
    man = json.loads((EXP / "manifest.json").read_text())
    if args.site:
        return site(man)
    ctx_commit = git("log", "-1", "--format=%H", "origin/main", "--", "schemas/plato.context.jsonld").strip()
    ctx, dom, rng, dt = schema(ctx_commit)
    with gzip.open(EXP / "deep-plato.jsonl.gz", "rt", encoding="utf-8") as f:
        gaz = json.loads(f.readline())["gazetteer"]
    init(ctx, dom, rng, dt, gaz)
    if args.county:
        for ln in county(args.county)[1]:
            print(ln)
        return

    # the inputs must be the files the manifest describes
    inputs = sorted((f for f in man["files"] if re.fullmatch(r"plato/deep-plato-\d\d\.json\.gz", f["file"])), key=lambda f: f["file"])
    bad = [f["file"] for f in inputs if sha256(EXP / f["file"]) != f["sha256"]]
    if len(inputs) != 66 or bad:
        sys.exit(f"inputs do not match the manifest: {len(inputs)} county files listed, digests differ for {bad[:3]}")
    codes = [re.search(r"(\d\d)\.json", f["file"]).group(1) for f in inputs]
    t0 = time.time()
    out = EXP / OUT_NAME
    seen, n, per = set(), 0, {}
    with gzip.open(out, "wt", encoding="utf-8", compresslevel=6) as fh:
        fh.write(f"# DEEP (the English Place-Name Society survey, digitised 2011-13, Jisc 2017 release) as RDF, from the PLATO export.\n"
                 f"# plato_commit={man['plato_commit']} (records validated against PLATO {man.get('plato_tag') or ''}); "
                 f"JSON-LD context and ontology from PLATO commit {ctx_commit}.\n"
                 f"# Licence: CC BY-NC 4.0 (https://creativecommons.org/licenses/by-nc/4.0/): an adaptation of the DEEP MADS data. "
                 f"Built by process/export_rdf.py, github.com/WorldHistoricalGazetteer/epns.\n")
        with Pool(args.procs, initializer=init, initargs=(ctx, dom, rng, dt, gaz)) as pool:
            for cc, lines in pool.imap(county, codes):
                k = 0
                for ln in lines:
                    own = f"<https://w3id.org/whg-epns/{cc}/"
                    if ln.startswith(SHARED) or (ln.startswith(GAZ_IRI) and "contains_" not in ln) \
                            or (ln.startswith("<") and not ln.startswith(own) and f" {RDFTYPE} " in ln):   # GeoNames, Getty: typed in several counties
                        if ln in seen:
                            continue
                        seen.add(ln)
                    fh.write(ln + "\n")
                    k += 1
                per[cc] = k
                n += k
                print(f"  {cc} {k:>10,} triples  {time.time() - t0:5.0f}s", flush=True)
    entry = {"file": OUT_NAME, "bytes": out.stat().st_size, "sha256": sha256(out)}
    man["files"] = [f for f in man["files"] if f["file"] != OUT_NAME] + [entry]
    man["rdf"] = {"file": OUT_NAME, "triples": n, "context_commit": ctx_commit, "ontology_commit": ctx_commit,
                  "per_county": per, "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (EXP / "manifest.json").write_text(json.dumps(man, indent=1))
    print(f"{n:,} triples -> {out} ({entry['bytes'] / 1e6:.0f} MB) in {time.time() - t0:.0f}s; context {ctx_commit[:12]}")


def site(man):
    """The browser's per-record RDF view (docs/js/rdf.js) needs the same context and rules as the corpus file,
    and a fixture to prove it produces the same graph: the seven records of docs/data/plato-sample.json, as
    canonical N-Quads (RDFC-1.0 / URDNA2015), so blank-node labels do not matter."""
    from pyld import jsonld
    commit = man["rdf"]["context_commit"]
    ctx, dom, rng, dt = schema(commit)
    init(ctx, dom, rng, dt, None)
    out = ROOT / "docs" / "data" / "rdf"
    out.mkdir(parents=True, exist_ok=True)
    (out / "context.jsonld").write_text(json.dumps({"@context": ctx}, ensure_ascii=False, separators=(",", ":")))
    (out / "rules.json").write_text(json.dumps({"context_commit": commit, "domain": dom, "range": rng, "datatype": dt,
                                                "bounds": sorted(BOUNDS), "gazetteer": "https://w3id.org/whg-epns/"}, indent=1))
    sample = json.loads((ROOT / "docs" / "data" / "plato-sample.json").read_text())
    recs = {}
    for rid, r in sample["records"].items():
        doc = {"gazetteer": {"@id": "https://w3id.org/whg-epns/"}, "spatialEntities": [r["plato"]]}
        if r.get("identityRelations"):
            doc["identityRelations"] = r["identityRelations"]
        nq = "\n".join(triples(doc)) + "\n"
        recs[rid] = {"triples": nq.count("\n"), "canonical": jsonld.normalize(
            nq, {"algorithm": "URDNA2015", "inputFormat": "application/n-quads", "format": "application/n-quads"})}
    (out / "sample.json").write_text(json.dumps({"context_commit": commit, "plato_commit": sample["plato_commit"], "records": recs}, ensure_ascii=False))
    print(f"docs/data/rdf/: context and rules at PLATO {commit[:12]}; {len(recs)} canonical fixture graphs, "
          f"{sum(r['triples'] for r in recs.values()):,} triples")


if __name__ == "__main__":
    main()

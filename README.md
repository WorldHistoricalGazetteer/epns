# DEEP — the English place-name survey, as data and as a map

The [English Place-Name Society](https://www.nottingham.ac.uk/research/groups/epns/) survey volumes, as
digitised in 2011–13 by DEEP (Digital Exposure of English Place-names) and published in 2017 by Jisc,
the UK higher-education technology body, as MADS XML, turned into:

1. a **DuckDB database and Parquet tables** for analysis (`process/build_db.py`), and
2. a **static single-page explorer** for GitHub Pages (`docs/`): full-text and phonetic search over
   every headword and historical spelling, the administrative hierarchy county → hundred → parish →
   township → minor names and field-names, the attestations set out as the volumes print them, and
   a MapLibre map with CARTO, OpenStreetMap and Ordnance Survey six-inch (NLS) basemaps.

**Live site:** https://worldhistoricalgazetteer.github.io/epns/

This is a second, machine-readable route into the material, not a replacement for the first: the
Institute for Name-Studies at Nottingham has continued to develop its [Digital Survey of English
Place-Names](https://www.nottingham.ac.uk/research/groups/ins/resources/digital-survey-of-english-place-names.aspx)
since 2013, and its [Key to English Place-Names](https://kepn.nottingham.ac.uk/) holds the etymologies.
What is here is the 2017 release as open data, with persistent identifiers, because the 2013 project's
own record URIs no longer resolve.

> **Data licence.** "Digitisation of English Placenames MADS data is licensed to Jisc by the English
> Place Names Society and released under a Creative Commons Attribution-NonCommercial 4.0 International
> License." — as published at http://mads.digitalresources.jisc.ac.uk/mads2017/. Everything in
> `docs/data/` derives from it and carries the same licence. The code here is BSD 3-Clause (see
> `LICENSE`, which also lists the third-party components under `docs/symphonym/`).

## What the data is

66 XML files, one per survey volume (423 MB), each a MADS `<madsCollection>` of `<mads>` records:

| | Count |
|---|---|
| Place records | 539,372 |
| of which field-names (no spellings or coordinates of their own) | 378,543 |
| Name forms (headword + variant spellings, each with a URI) | 820,567 |
| Dated citations of a spelling in a source (the survey's "attestations") | 429,536 |
| Attestations in PLATO's sense in the export (every sourced claim: headwords, citations, normalised forms, coordinates, hierarchy) | 1,414,328 |
| Dates on attestations | 441,237 |
| Normalised search forms | 391,777 |
| Distinct source abbreviations (the bibliography itself was never published) | 7,049 |
| Places with their own coordinates | 23,448 (from EPNS, KEPN, GeoNames and Unlock, often disagreeing) |

A record has a headword, exactly one `broader` link to its parent, optional variant spellings, and an
`extension` holding the scholarly content: attestations (one or more structured dates, a source
abbreviation, page/item/folio/manuscript apparatus, copy-dates, a personal-name marker), search
terms, coordinates and notes. Six per cent of attestations are nested to express *et passim* runs.
The full profile, and why LPF cannot hold it without loss, is written up in
[LinkedPasts/linked-places-format#53](https://github.com/LinkedPasts/linked-places-format/discussions/53).

## Downloads and exports

The [downloads page](https://worldhistoricalgazetteer.github.io/epns/downloads.html) offers the whole corpus in four
shapes, all built from the same DuckDB by `process/export_plato.py`, `process/export_rdf.py` and `process/build_downloads.py`
and attached to a GitHub release (they are too large and too rarely rebuilt for git history):

| Format | For | Loss |
|---|---|---|
| **PLATO** place-centric JSON (JSON Lines whole-corpus file; 66 per-county documents) | anyone who wants the attestations | none: every element of every record, validated against the schema at a named PLATO commit |
| **RDF**, N-Triples (`deep-plato.nt.gz`) | triple stores, SPARQL | none beyond PLATO's: the PLATO files expanded with PLATO's own JSON-LD context |
| **Parquet** tables and the **DuckDB** database | counting, joining, plotting | none |
| **LPF** v1.3 (GeoJSON Text Sequence; 66 per-county FeatureCollections) | GIS and gazetteer interoperability | lossy, and 95.7% of features have null geometry; the page says exactly what is dropped |

Every PLATO and LPF file carries `plato_commit=<sha>` in its header and the manifest carries the same
sha; `export_plato.py --verify` fails loudly if they disagree. The exporter reads the schema out of that
exact commit with `git show`, so the stamp names what validated the output. Where the commit carries a
release tag whose name matches `owl:versionInfo` the files also cite the version (the current exports
are against PLATO v0.4.0, the first release to carry `plato:Citation` with locator and attribution
status, `source_timespan`, `derived_from`, `occurrence_count`, `occurrence_context` and
`form_status`); where it does not, the files say so rather than quote a version string that does not
describe them.

The triples come from the validated PLATO files, whose digests `export_rdf.py` checks against the
manifest first, expanded by PLATO's own context (the latest commit of `schemas/plato.context.jsonld`,
which postdates v0.4.0). The context's `$comment` lists what a context cannot do; the exporter does three
of those things by rule from `ontology.ttl` at the same commit. It types every node by the rdfs:domain and
rdfs:range of the predicates that touch it (single named `plato:` classes only), gives typed literals
their declared XSD datatype and timespan bounds `xsd:gYear`, and writes representative points as
`geo:wktLiteral`. Names get no language tag, since the spellings are several languages in one field.
The 66 county files share one gazetteer node, described once from the corpus header; a copy witness's
date is named `<witness>#timespan`, so it is written once rather than once per citing record; blank nodes
are relabelled per county.

Each place page on the map also shows its own record **as PLATO**, **as LPF** (with the losses struck
through in place) and **as MADS** (regenerated), generated in the browser by `docs/js/formats.js`, a
port of the Python exporter. `docs/data/plato-sample.json` is the Python's output for six records;
the headless checks require the browser port to produce identical objects for them.

## Identifiers

The DEEP record id (`epns-deep-<county>-<code>-<type>-<seq>`) is read verbatim from the XML's `<mads ID>`
attribute, never composed; all 539,372 are unique, all match the pattern, and parse-then-compose
round-trips every one, which `build_db.py` asserts on every build. The sequence numbers are DEEP's own
(not dense, not in document order, so not assigned here). The site's integer `gid` is a build artefact
used only inside its data files.

DEEP also published a URI for every record and every name form, `http://placenames.org.uk/id/placename/
<county>/<serial>`, numbered in one county-wide sequence. That domain has changed hands and now serves
an unrelated commercial site; the project's record pages at `epns.nottingham.ac.uk` answer 200 with the
university home page for every path. The exports and the per-record files therefore identify records
and name forms as **`https://w3id.org/whg-epns/<county>/<serial>`**: DEEP's own pair, unchanged, under a
w3id namespace registered by the World Historical Gazetteer (decision of 26 September 2026), so a 2013
URI maps to its w3id by prefix substitution alone. The original DEEP URI is kept on every record as
provenance. Until the registration is merged at `perma-id/w3id.org` the URIs are declared but not yet
resolvable; the export manifest says so.

## Persistent identifiers, prepared

The site can address any record by DEEP's own county-wide serial, the number in the record's 2013 URI:
`#u=02/000002` opens Bunsty Hundred (`http://placenames.org.uk/id/placename/02/000002`). For the 15,587
records at parish level and above, `docs/id/<county>/<serial>.json|.geojson|.xml` are static PLATO,
LPF and original-MADS representations (`process/export_records.py`), so a resolver that content-
negotiates can land every branch on a file that exists. Minor names and field-names have no static
machine file (three formats for every record would be 1.6 million files); their HTML view generates
the formats in the browser, and a machine request for one answers 404 with a page that carries a person
to the record. That cut is deliberate and stated rather than hidden behind a redirect to a neighbour.

The w3id namespace `whg-epns` (branch `whg-epns` on the owner's fork of `perma-id/w3id.org`) resolves
`/{county}/{serial}` by content negotiation to these files, the record's map view, or the original MADS,
and `/data/*` to the latest release. A name form's serial resolves to the record that carries it.

### Sources

Every citation's source has an IRI too, so the exports triplify to a graph in which a source is one
node, not 414,770 anonymous copies of one (`process/sources.py` holds the rule, and every writer
imports it):

| IRI | What it names |
|---|---|
| `https://w3id.org/whg-epns/source/<abbrev>` | a **national** source, one archive series or printed work whichever volume cites it (`source/DB`, `source/Pat`); a curated list of 78 |
| `…/source/<county>/<DEEP source id>` | a source **as one county volume cites it** (`source/52/do41`); `…/source/<county>/x-<abbrev>` where DEEP gave no id |
| `<either>/witness/<ms>-<copy date>` | the copy a form is read in, derived from the work (`source/ASC/witness/B-c-1000`) |
| `…/volume/<county>` | the county volume |
| `…/agent/deep` | the DEEP project as the **agent** that made the 2013 GeoNames matches (`plato:Contributor`), kept apart from the dataset, which is evidence |
| `…/source/gazetteer/<name>`, `…/source/deep` | the coordinate gazetteers, and the DEEP project: `authorityType` "dataset", so `plato:Dataset`, not `plato:Source` |

Breadth of citation cannot tell the two kinds apart (tithe awards are cited in 59 counties, court
rolls in 43), and an abbreviation such as *Ct* or *TA* names a kind of record, not a document, so
*Ct* in Cheshire and *Ct* in Dorset must not share an IRI. The national list is therefore curated from
the abbreviations cited in 20 or more volumes, and anything off it is treated as the county's own:
more nodes rather than false merges. Path segments keep ASCII letters and digits and turn any other run
into one hyphen; a `?` in a copy date is kept as `q`, because `?14` and `14` are different claims.
Where DEEP spells one source id several ways, the IRI's title is the commonest spelling; the MADS keeps
the rest. `export_records.py` refuses to write if any IRI would carry two different descriptions.

Each IRI dereferences to `docs/id/<path>.json` (10,353 files): the source as JSON-LD, with PLATO's own
term definitions for a citation's source lifted into the file's context from the latest commit of
PLATO's JSON-LD context, which postdates the v0.4.0 release the records validate against. The build
round-trips a sample through expansion and compaction and requires them unchanged.

## Layout

```
process/fetch_data.sh             wget mirror of the 66 volumes -> data/mads2017/   (data/ is git-ignored)
process/build_db.py               XML -> data/parquet/*.parquet + data/deep.duckdb, with two-instrument count checks
process/export_site.py            DuckDB -> docs/data/ (core index, per-county files, name index, manifest)
process/build_symphonym_index.py  name keys -> int8 Symphonym v8 matrix, docs/data/symphonym/ (system python)
process/export_plato.py           DuckDB -> data/export/: PLATO (validated) + LPF, stamped with the PLATO commit; --sample, --verify
process/build_downloads.py        data/export + parquet + duckdb -> release bundles and docs/downloads.html (sizes/digests read, not typed)
process/export_rdf.py             data/export PLATO files -> deep-plato.nt.gz (N-Triples via PLATO's JSON-LD context), appended to the manifest
process/export_records.py         per-record static PLATO/LPF/MADS for parish level and above -> docs/id/<county>/<serial>.*,
                                  and one JSON-LD file per source IRI -> docs/id/source/**, docs/id/volume/<county>.json
process/sources.py                the source-IRI rule and the national list, imported by every writer
process/rehome.sh                 one-pass rewrite of every absolute URL/key after a repository transfer or rename
docs/                             the Pages site: index.html, css/, js/, symphonym/ (model + tokeniser), data/
tools/pages/shot.py               headless Playwright checks of the site (system python)
```

## Rebuilding

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
process/fetch_data.sh                                   # once; 423 MB
.venv/bin/python process/build_db.py                    # ~45 s; fails loudly if any count disagrees
.venv/bin/python process/export_site.py                 # ~2 min -> docs/data/
/usr/bin/python3 process/build_symphonym_index.py       # ~6 min; needs torch + onnxruntime + the indexing repo's tokeniser
.venv/bin/python process/export_plato.py                # ~25 min with validation -> data/export/ (+ --sample for the fixture)
.venv/bin/python process/export_rdf.py                  # ~12 min on 12 processes -> data/export/deep-plato.nt.gz
.venv/bin/python process/build_downloads.py --tag data-YYYY-MM-DD   # bundles + docs/downloads.html; then gh release upload
/usr/bin/python3 tools/pages/shot.py --serve            # headless checks; --prove-it-fails to check the checks
```

`build_db.py` compares the parser's totals with byte-level regex counts over the same files, and with
the fixed figures from the profile above, and exits non-zero if they disagree. `export_site.py` writes a
manifest with the sha256 of every file; the page uses those digests as IndexedDB cache keys, so a
rebuild invalidates exactly what changed.

## The database

`data/deep.duckdb` holds one table per Parquet file — `place`, `name`, `attestation`, `attestation_date`,
`passim`, `searchterm`, `geo`, `note` — plus three derived tables: `source` (the abbreviation registry
reconstructed from usage, per county), `place_tree` (each place's county / hundred / parish / township)
and `place_point` (one preferred coordinate per place, own or inherited, with the source recorded).

```sql
-- every attestation of a spelling of Cambridge, in date order
SELECT n.toponym, d.text AS date, a.source_text, a.page
FROM place p JOIN name n USING (place_id)
JOIN attestation a ON list_contains(a.variant_ids, n.name_id)
JOIN attestation_date d USING (attestation_id)
WHERE p.title = 'Cambridge' AND p.type = 'countytown' ORDER BY d.begin;
```

## The site

Static, server-less, "fetch only what you need". At boot it loads the 161k non-field-name places
(`core.json`, columnar); a county's full detail (`county/<cc>.json`) is fetched when a place there is
opened; the name index (`names/`) when you first search; and the phonetic model and matrix only when
the toggle is on. All of it is cached in IndexedDB under the manifest's digests.

Phonetic matching is [Symphonym v8](https://doi.org/10.5281/zenodo.22767194), run in the browser with
onnxruntime-web. The model, vocabularies and tokeniser port are copied unmodified from
[whg3](https://github.com/WorldHistoricalGazetteer/whg3) and hash-checked on load; the offline matrix
is built with the canonical Python tokeniser and the same ONNX graph, and both sides are checked
against the same golden fixture, so a query and the corpus are never tokenised two different ways.

Tile keys live in `docs/js/config.js` and are committed on the same basis as the sibling sites: they
are bound by the provider to this site's origin. The OS six-inch sheets come keyless from the National
Library of Scotland.

## Citing

Survey data: English Place-Name Society, digitised by DEEP and published by Jisc, CC BY-NC 4.0. Cite the
county volume for any name; each place page shows its DEEP record identifier. This repository:
Gadd, Stephen. 2026. *DEEP — the English place-name survey, as data and as a map*.
https://github.com/WorldHistoricalGazetteer/epns.

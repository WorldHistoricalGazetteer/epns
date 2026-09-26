# DEEP — the English place-name survey, as data and as a map

The [English Place-Name Society](https://www.nottingham.ac.uk/research/groups/epns/) survey volumes, as
digitised by the DEEP project (Digital Exposure of English Place-names, 2013) and published by Jisc in
MADS XML, turned into:

1. a **DuckDB database and Parquet tables** for analysis (`process/build_db.py`), and
2. a **static single-page explorer** for GitHub Pages (`docs/`): full-text and phonetic search over
   every headword and historical spelling, the administrative hierarchy county → hundred → parish →
   township → minor names and field-names, the attestations set out as the volumes print them, and
   a MapLibre map with CARTO, OpenStreetMap and Ordnance Survey six-inch (NLS) basemaps.

**Live site:** https://docuracy.github.io/deep/

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
| Attestations (dated citations of a spelling in a source) | 429,536 |
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

## Layout

```
process/fetch_data.sh             wget mirror of the 66 volumes -> data/mads2017/   (data/ is git-ignored)
process/build_db.py               XML -> data/parquet/*.parquet + data/deep.duckdb, with two-instrument count checks
process/export_site.py            DuckDB -> docs/data/ (core index, per-county files, name index, manifest)
process/build_symphonym_index.py  name keys -> int8 Symphonym v8 matrix, docs/data/symphonym/ (system python)
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
https://github.com/docuracy/deep.

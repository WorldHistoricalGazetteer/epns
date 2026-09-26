# Draft PR body for perma-id/w3id.org — namespace `whg-epns`

*Branch `whg-epns` on the fork `docuracy/w3id.org` (synced to upstream 57b211f1). Files: `ids/whg-epns/.htaccess`, `ids/whg-epns/README.md`. Not yet pushed or opened; the owner does that.*

---

**New namespace: `whg-epns`** — persistent identifiers for the DEEP digitisation of the English Place-Name Society survey.

**What the corpus is.** Between 2011 and 2013 the Jisc-funded *Digital Exposure of English Place-names* (DEEP) project converted the county volumes of the English Place-Name Society survey into structured XML: 539,372 place records and 1,414,328 dated attestations of their historical spellings, from Domesday Book onwards. Jisc released the files in 2017 under CC BY-NC 4.0. It is the reference dataset for English toponymy, and it is cited in scholarship by the identifiers the project published.

**Why the identifiers need a home.** DEEP published a URI for every record and every name form, `http://placenames.org.uk/id/placename/{county}/{serial}`. That domain has since changed hands and now serves an unrelated commercial site, and the project's record pages at `epns.nottingham.ac.uk` answer HTTP 200 with the university home page for every path, so a naive link-checker calls both fine. The corpus has no working external identifier at all. This namespace gives the original scheme a resolvable form: **the path `{county}/{serial}` is DEEP's own pair, carried unchanged**, so any citation made against a 2013 URI maps to its w3id by prefix substitution alone.

**What resolves.** Everything targets static files served by GitHub Pages from `WorldHistoricalGazetteer/epns`, which cannot negotiate content, so the `.htaccess` does all the negotiation and every branch lands on a file that exists: `text/html` to the record on the map; JSON / JSON-LD (and `*/*`, per the reasoning in `ids/whg/.htaccess`) to the record as a PLATO place-centric document; `application/geo+json` to a Linked Places Format FeatureCollection; XML to the original MADS element; explicit `.html/.json/.geojson/.xml` suffixes alongside. The namespace root and `/downloads` go to the site; `/data/{plato,plato-counties,parquet,duckdb,lpf,lpf-counties,manifest}` go to the **latest** data release, so regeneration needs no change here. Per-record machine files exist for the 15,587 records at parish level and above; for minor names, field-names and name-form serials the HTML view is always available and a machine request answers 404 with a page that carries a person to the record. The README states that cut and why.

**Governance.** Registered and maintained by the World Historical Gazetteer, which built and hosts the resolver targets. Contact details are in the README.

**Checks.** Rules follow the house style of `ids/whg/.htaccess` (same `*/*` handling, same 404 fallback). Targets verified live: `https://worldhistoricalgazetteer.github.io/epns/#u=02/000002` (Bunsty Hundred), `…/epns/id/02/000002.json|.geojson|.xml`, and the release assets.

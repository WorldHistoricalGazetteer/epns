/* One record as RDF, in the browser: a port of process/export_rdf.py's triples(), so that the triples shown
 * for a record are the ones deep-plato.nt.gz holds for it.
 *
 *   1. The record's PLATO (formats.js toPlato, already proved identical to the Python export) is put in a
 *      place-centric document under the dataset node, https://w3id.org/whg-epns/, and expanded by jsonld.js
 *      with PLATO's own JSON-LD context: docs/data/rdf/context.jsonld, pinned to the commit the published
 *      corpus file was built with (docs/data/rdf/rules.json says which).
 *   2. In the expanded tree, a witness's date is named <witness>#timespan and a representative point becomes
 *      a geo:wktLiteral, as the corpus exporter does.
 *   3. Every node is typed by the rdfs:domain / rdfs:range of the predicates that touch it, and plain literals
 *      get the XSD datatype the ontology declares (timespan bounds: xsd:gYear or xsd:date by shape). The rules
 *      are extracted from ontology.ttl at the same commit by export_rdf.py --site, not written here.
 *
 * The harness canonicalises this module's output for the seven fixture records and requires it to equal the
 * Python's canonical graphs (docs/data/rdf/sample.json). jsonld.js is loaded on first use only.
 */
const P = 'https://w3id.org/plato#';
const XSD = 'http://www.w3.org/2001/XMLSchema#';
const RDFTYPE = '<http://www.w3.org/1999/02/22-rdf-syntax-ns#type>';
const WKT = 'http://www.opengis.net/ont/geosparql#wktLiteral';
const LEX = {
  dateTime: /^-?\d{4,}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:\d\d)?$/,
  anyURI: /^[A-Za-z][A-Za-z0-9+.-]*:\S+$/,
  integer: /^[+-]?\d+$/,
};
const PLAIN = /^(\S+) (<[^>]+>) "((?:[^"\\]|\\.)*)" \.$/;
const JSONLD = 'https://cdn.jsdelivr.net/npm/jsonld@8.3.3/+esm';

let loaded = null;
async function load() {
  if (!loaded) {
    loaded = Promise.all([
      import(JSONLD).then((m) => m.default || m),
      fetch('data/rdf/context.jsonld').then((r) => r.json()),
      fetch('data/rdf/rules.json').then((r) => r.json()),
    ]).then(([jsonld, ctx, rules]) => ({ jsonld, ctx: ctx['@context'], rules, bounds: new Set(rules.bounds) }))
      .catch((e) => { loaded = null; throw e; });
  }
  return loaded;
}

function enrich(node) {
  if (Array.isArray(node)) { node.forEach(enrich); return; }
  if (!node || typeof node !== 'object') return;
  const ts = P + 'source_timespan';
  if ((node['@id'] || '').includes('/witness/') && node[ts]) {
    for (const t of node[ts]) if (t && typeof t === 'object' && !('@id' in t)) t['@id'] = node['@id'] + '#timespan';
  }
  const k = P + 'repr_point';
  if (node[k]) {
    node[k] = node[k].map((v) => {
      const xs = (v && v['@list'] || []).map((i) => i['@value']);
      return xs.length === 2 ? { '@value': `POINT(${xs[0]} ${xs[1]})`, '@type': WKT } : v;
    });
  }
  for (const v of Object.values(node)) if (v && typeof v === 'object') enrich(v);
}

/* The Python writes numbers the way Python prints them; JSON-LD's canonical double form is the same in both
   libraries, so no reformatting is needed here. */
export async function toRdf(entity, identityRelations) {
  const { jsonld, ctx, rules, bounds } = await load();
  const doc = { '@context': ctx, gazetteer: { '@id': rules.gazetteer }, spatialEntities: [entity] };
  if (identityRelations && identityRelations.length) doc.identityRelations = identityRelations;
  const exp = await jsonld.expand(doc);
  enrich(exp);
  const nq = await jsonld.toRDF(exp, { format: 'application/n-quads' });
  const out = [];
  const types = new Set();
  for (let ln of nq.split('\n')) {
    if (!ln) continue;
    const sp1 = ln.indexOf(' '), sp2 = ln.indexOf(' ', sp1 + 1);
    const s = ln.slice(0, sp1), p = ln.slice(sp1 + 1, sp2), rest = ln.slice(sp2 + 1);
    const m = PLAIN.exec(ln);
    if (m) {
      const lex = m[3];
      let t = null;
      if (bounds.has(p)) t = /^-?\d{4,}$/.test(lex) ? 'gYear' : /^-?\d{4,}-\d\d-\d\d$/.test(lex) ? 'date' : null;
      else if (rules.datatype[p] && LEX[rules.datatype[p]].test(lex)) t = rules.datatype[p];
      if (t) ln = `${s} ${p} "${lex}"^^<${XSD}${t}> .`;
    }
    out.push(ln);
    if (rules.domain[p]) types.add(`${s} ${RDFTYPE} ${rules.domain[p]} .`);
    const o = rest.slice(0, -2);
    if (rules.range[p] && (o.startsWith('<') || o.startsWith('_:'))) types.add(`${o} ${RDFTYPE} ${rules.range[p]} .`);
  }
  const have = new Set(out);
  const lines = out.concat([...types].filter((t) => !have.has(t)).sort());
  return { text: lines.join('\n') + '\n', n: lines.length, commit: rules.context_commit };
}

/* RDFC-1.0 (URDNA2015) canonical N-Quads: blank-node labels made comparable, for the harness. */
export async function canonical(ntriples) {
  const { jsonld } = await load();
  return jsonld.canonize(ntriples, { algorithm: 'URDNA2015', inputFormat: 'application/n-quads', format: 'application/n-quads' });
}

# PR body for perma-id/w3id.org — `whg-epns`: release identifiers

*Branch `whg-epns-releases` on the fork `docuracy/w3id.org`, one commit (ce8d11de, rebased on upstream 79d537ca).
Files: `ids/whg-epns/.htaccess` (+7 lines), `ids/whg-epns/README.md` (+3/-1). Opened together with the
`plato` namespace's versioned-IRI request, by the maintainer listed for both directories.*

---

**`whg-epns`: identifiers for each frozen data release** (follow-up to #6754, merged)

**What is added.** Two rules under the existing namespace, and two rows in its README:

| Path | Behaviour |
|---|---|
| `/release/{name}` | 303 to that data release's page on GitHub: `/release/data-2026-09-28` → `github.com/WorldHistoricalGazetteer/epns/releases/tag/data-2026-09-28`. |
| `/release/{name}/{file}` | 303 to one file of that release, fixed for good: `/release/data-2026-09-28/deep-plato.nt.gz`. |

`{name}` is `data-YYYY-MM-DD`; `{file}` is one path segment of `[A-Za-z0-9._-]`. Nothing else changes: the record,
source, agent, volume and `/data/*` rules and the 404 fallback are as merged in #6754.

**Why.** The data now describes itself as a series of frozen snapshots, as PLATO 0.5.0 requires of a published
gazetteer (append-only: an attestation is never changed in place, only superseded by a new one in a new release).
Each release's header names its own snapshot IRI and links the series:

```json
"gazetteer": {
  "@id": "https://w3id.org/whg-epns/release/data-2026-09-28",
  "version": "data-2026-09-28", "status": "published",
  "isVersionOf": "https://w3id.org/whg-epns/",
  "previousVersion": "https://w3id.org/whg-epns/release/data-2026-09-26"
}
```

These IRIs are therefore dereferenced from data (by JSON-LD processors following `dcat:isVersionOf` and
`dcat:previousVersion`), not only typed by people, and they must not move when `/data/*`, which always
means the latest release, moves on. `/release/{name}/{file}` gives a citation a file that will not change.

**Targets verified live** before opening: `…/releases/tag/data-2026-09-26` and `…/data-2026-09-28` answer 200;
`…/releases/download/data-2026-09-28/deep-plato.nt.gz` answers 302 to the asset. The two regexes were checked
against every path they should match, with and without a trailing slash, and against malformed paths
(`release/x`, `release/data-2026-09-28/../x`, `release/data-2026-09-28/a/b`), none of which match.

**Governance.** As #6754: registered and maintained by the World Historical Gazetteer; contact details in the
README are unchanged.

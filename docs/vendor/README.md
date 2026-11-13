# docs/vendor: pinned third-party code, served from this site

These were loaded from CDNs (unpkg, jsdelivr) until 13 Nov 2026. They are now copies of the SAME pinned
versions, so the page starts with no CDN and no network once cached (see docs/sw.js). Nothing here is
edited by hand; each licence file is kept beside its code. Replace a library by repeating the recipe, never
by editing it, and update the version and digest below.

| Library | Version | Licence | File | sha256 |
|---|---|---|---|---|
| maplibre-gl | 4.7.1 | BSD-3-Clause | `maplibre-gl/maplibre-gl.js` | `e877fe22fdfac649cdf4cac27d66e4cb4872c5960adcd9229a9eb993c22dafb8` |
| maplibre-gl | 4.7.1 | BSD-3-Clause | `maplibre-gl/maplibre-gl.css` | `576b085fdd9487a65a19215328c1e086c07ce5bf6da09b666b3806d3d008dae9` |
| onnxruntime-web | 1.27.0 | MIT | `onnxruntime-web/ort.wasm.min.mjs` | `8ccdb07fb2efe638864c8838d09a203007b83d2c32a5bc431d20d71eca1bf627` |
| onnxruntime-web | 1.27.0 | MIT | `onnxruntime-web/ort-wasm-simd-threaded.mjs` | `0a1e718d99c41b22c21f2520ff4f9e883a6b5533856e398d21816ee8eb8185d3` |
| onnxruntime-web | 1.27.0 | MIT | `onnxruntime-web/ort-wasm-simd-threaded.wasm` | `d1ab1b94b16a65b29d710d0b587b29e7bed336827577623913479b8afe8113e6` |
| any-ascii | 0.3.3 | ISC | `any-ascii/any-ascii.mjs` | `4448714cd4f8dc30d95ba33a38d209abaa99c10c44ac3fb16f6010689b40e1e7` |
| jsonld | 8.3.3 | BSD-3-Clause | `jsonld/jsonld.mjs` | `8348f2e63e331a5a5fd96101838b67e198bfe69a5bf05caa59fc8abcc8f8074d` |

## Recipe
- **maplibre-gl, onnxruntime-web**: `npm install maplibre-gl@4.7.1 onnxruntime-web@1.27.0`, then copy `dist/maplibre-gl.{js,css}` and
  `dist/ort.wasm.min.mjs`, `dist/ort-wasm-simd-threaded.{mjs,wasm}` unchanged. Only the trailing `sourceMappingURL` comments were
  removed, so the browser does not ask for maps that are not here.
- **any-ascii**, **jsonld**: neither ships a single browser file (the CDN's `+esm` build is assembled on the fly from many
  modules), so each was bundled with `esbuild@0.24.0 --bundle --format=esm --minify` from the npm package at that version
  (`jsonld` with `--platform=browser --define:global=globalThis --legal-comments=eof`). The default export is the library.
  The browser-parity harness (`tools/pages/shot.py --check formats`) is what shows the jsonld bundle behaves: it requires
  the browser's RDF graph to equal Python's, after RDFC-1.0 canonicalisation.
- `symphonym/preprocess.js` is hash-pinned to whg3's and still imports any-ascii from cdn.jsdelivr.net. `sw.js` answers that
  one request from `vendor/any-ascii/` (same version), so the file stays unmodified. Without the service worker it still
  uses the CDN.
- ORT's package ships no LICENSE file; `onnxruntime-web/LICENSE` is the standard MIT text with the upstream copyright line.

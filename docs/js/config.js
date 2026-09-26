/* Keys for the tile providers that need one. THIS FILE IS COMMITTED, on the same basis as the
 * sibling repositories (premodern-rivers docs/js/config.js, D-075 there): a key in a static
 * page is public the moment the page is served, so the only keys that may live here are ones
 * bound to this site's origin, https://docuracy.github.io, by the provider. That binding, not
 * secrecy, is the control.
 *
 *   carto   CARTO Basemaps. Required since 2026-08: keyless requests come back HTTP 200 with
 *           "API KEY REQUIRED" printed across the tile, a failure that reports success. The key
 *           is the one the sibling sites use, restricted at carto.com to docuracy.github.io. If
 *           the watermark ever appears on the published page the restriction has changed and the
 *           key wants replacing, not debugging. With it null the CARTO option is simply not offered.
 *
 *   The OS six-inch sheets need NO key: the National Library of Scotland serves them itself from
 *   mapseries-tilesets.s3.amazonaws.com (verified across England and Wales by the premodern-rivers
 *   project, 2026-09-01), which also means they work in a local preview, where an origin-bound
 *   MapTiler key would 403 silently. OpenStreetMap needs no key either.
 */
export const CONFIG = {
  cartoKey: 'cb1_2e5i_1_2d303d63fc7c8b2560a39a12',   // CARTO_API_KEY, domain-restricted
};

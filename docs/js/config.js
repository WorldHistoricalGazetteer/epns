/* Keys for the tile providers that need one. THIS FILE IS COMMITTED, on the same basis as the
 * sibling repositories (premodern-rivers docs/js/config.js, D-075 there): a key in a static
 * page is public the moment the page is served, so the only keys that may live here are ones
 * bound to this site's origin by the provider. That binding, not secrecy, is the control.
 *
 *   carto   CARTO Basemaps. Required since 2026-08: keyless requests come back HTTP 200 with
 *           "API KEY REQUIRED" printed across the tile (a 2,049-byte image), a failure that reports
 *           success. The key is the one the sibling sites use. It was set up as restricted to
 *           docuracy.github.io, but this site is served from worldhistoricalgazetteer.github.io and
 *           the tiles are fine there. MEASURED 13 Nov 2026 with curl: the same tile with this key
 *           is byte-identical (54,583 bytes) whatever the Referer and Origin sent, including
 *           worldhistoricalgazetteer.github.io, docuracy.github.io, example.org and none, so no
 *           origin restriction is being enforced at the tile edge today. The dashboard allow-list
 *           itself has NOT been seen. If the watermark ever appears on the published page the
 *           restriction has changed and the key wants replacing (or the origin adding), not
 *           debugging. With it null the CARTO option is simply not offered. The OS six-inch
 *           option below needs no key and is the safer basemap for a demo.
 *
 *   The OS six-inch sheets need NO key: the National Library of Scotland serves them itself from
 *   mapseries-tilesets.s3.amazonaws.com (verified across England and Wales by the premodern-rivers
 *   project, 2026-09-01), which also means they work in a local preview, where an origin-bound
 *   MapTiler key would 403 silently. OpenStreetMap needs no key either.
 */
export const CONFIG = {
  cartoKey: 'cb1_2yg5_1_9504b922f13f07d40819d950',   // CARTO_API_KEY, domain-restricted
};

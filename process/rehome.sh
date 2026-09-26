#!/usr/bin/env bash
# Re-home the site after a repository transfer or rename. Run ONCE, after the transfer, from the repo root:
#
#     process/rehome.sh WorldHistoricalGazetteer epns https://worldhistoricalgazetteer.github.io/epns/ <carto-key-for-new-origin>
#
# GitHub redirects repository URLs (github.com/<old>/<old> -> new, release assets included) after a
# transfer, but it does NOT redirect Pages, so every absolute reference to the old Pages URL dies at
# the moment of the move. This script rewrites them all in one pass and rebuilds what embeds them:
#
#   docs/index.html, README.md, LICENSE      the links people read
#   docs/js/config.js                        the CARTO key, which is origin-restricted: the old key
#                                            returns watermarked tiles from any other origin
#   process/export_plato.py SITE, build_downloads.py REPO, tools/pages/shot.py default URL
#   docs/downloads.html                      regenerated (release links)
#   data/export + release                    the gazetteer @id inside every PLATO file is the site URL,
#                                            so the exports are regenerated and re-uploaded (~25 min)
#
# It does not transfer, rename or publish anything: those are done in the GitHub UI by the owner.
set -euo pipefail
ORG=${1:?org}; REPO=${2:?repo}; URL=${3:?pages url with trailing slash}; CARTO=${4:?carto key valid for the new origin}
OLD_URL="https://worldhistoricalgazetteer.github.io/epns/"; OLD_REPO="WorldHistoricalGazetteer/epns"
cd "$(dirname "$0")/.."
git grep -l "$OLD_URL\|$OLD_REPO" -- docs/index.html docs/404.html README.md LICENSE process tools | while read -r f; do
  sed -i "s#$OLD_URL#$URL#g; s#github.com/$OLD_REPO#github.com/$ORG/$REPO#g; s#\"$OLD_REPO\"#\"$ORG/$REPO\"#g" "$f"
done
sed -i "s#cartoKey: '[^']*'#cartoKey: '$CARTO'#" docs/js/config.js
sed -i "s#/deep/favicon.svg#/${REPO}/favicon.svg#" docs/404.html
echo "rewritten; now:"
echo "  1. re-enable Pages on the new repository (Settings -> Pages -> main:/docs) if the transfer dropped it"
echo "  2. .venv/bin/python process/export_plato.py && .venv/bin/python process/export_plato.py --sample && .venv/bin/python process/export_records.py"
echo "  3. .venv/bin/python process/build_downloads.py --tag <tag> && gh release upload <tag> --clobber data/export/{deep-plato.jsonl.gz,deep-plato-counties.tar,deep-parquet.tar,deep.duckdb.gz,deep-lpf.geojsonl.gz,deep-lpf-counties.tar,export-manifest.json} -R $ORG/$REPO"
echo "  4. commit, push, then: /usr/bin/python3 tools/pages/shot.py --url $URL"

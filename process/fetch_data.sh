#!/usr/bin/env bash
# Mirror the DEEP MADS XML (66 volumes, 423 MB) into data/mads2017/.
#
# The source is a plain Amazon S3 index page, not a git repository, so this is a wget mirror.
# data/ is git-ignored; run this once per clone, then process/build_db.py.
#
# Licence of what this fetches, verbatim from the index page:
#   "Digitisation of English Placenames MADS data is licensed to Jisc by the English Place Names
#    Society and released under a Creative Commons Attribution-NonCommercial 4.0 International License."
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data
wget -q --show-progress -r -np -nH -e robots=off -P data http://mads.digitalresources.jisc.ac.uk/mads2017/
n=$(ls data/mads2017/vol*.xml | wc -l)
echo "fetched $n volumes into data/mads2017/ ($(du -sh data/mads2017 | cut -f1))"
[ "$n" -eq 66 ] || { echo "expected 66 volumes (as listed on the index page in Sep 2026), got $n" >&2; exit 1; }

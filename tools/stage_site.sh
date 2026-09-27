#!/usr/bin/env sh
# Stages the docs site into build/site: only site/ plus the files the site needs, so the
# rest of the repo is never published. Used by .github/workflows/pages.yaml and local preview.
set -eu

root=$(cd "$(dirname "$0")/.." && pwd)
out="$root/build/site"

rm -rf "$out"
mkdir -p "$out"
cp -R "$root/site/." "$out/"
# The README's badges and QGIS logo sit above its title; the site starts at the title.
sed -n '/^# /,$p' "$root/README.md" > "$out/index.md"
[ -s "$out/index.md" ] || { echo "README.md has no '# ' heading to start the site from" >&2; exit 1; }
cp "$root/DEVELOPMENT.md" "$root/LICENSE.txt" "$out/"
cp "$root/PolygonsParallelToLine/icons/icon.png" "$out/assets/favicon.png"

echo "Staged site in $out"

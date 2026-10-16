#!/usr/bin/env bash
# Compile authored pages with pinned Typst; publish precomputed reports/evidence.
set -euo pipefail

gammabench_repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
gammabench_repo_dir="${GAMMABENCH_CONTENT_ROOT:-$gammabench_repo_dir}"
gammabench_destination="${1:-$gammabench_repo_dir/.gammabench/pages-site}"
gammabench_destination="$(realpath -m "$gammabench_destination")"
case "$gammabench_destination" in
  "$gammabench_repo_dir/snapshots/"*|"$gammabench_repo_dir/docs/results/"*|"$gammabench_repo_dir/web/"*|"$gammabench_repo_dir/src/"*)
    echo "Pages output must be outside its source evidence, reports and templates" >&2
    exit 1
    ;;
esac
if [[ -e "$gammabench_destination" ]]; then
  echo "Pages destination already exists: $gammabench_destination" >&2
  exit 1
fi
if ! command -v typst >/dev/null || [[ "$(typst --version)" != "typst 0.15.1 "* ]]; then
  echo "Building Pages requires Typst 0.15.1 on PATH" >&2
  exit 1
fi
mkdir -p "$(dirname "$gammabench_destination")"
gammabench_stage="$(mktemp -d "$(dirname "$gammabench_destination")/.pages-staging.XXXXXX")"
mkdir -p "$gammabench_repo_dir/.gammabench"
gammabench_catalog_dir="$(mktemp -d "$gammabench_repo_dir/.gammabench/pages-catalog.XXXXXX")"
trap 'rm -rf -- "$gammabench_stage" "$gammabench_catalog_dir"' EXIT
PYTHONPATH="$gammabench_repo_dir/src" python3 -m gammabench.cli catalog \
  --repo "$gammabench_repo_dir" --include-workspaces --output "$gammabench_catalog_dir/catalog.json"
gammabench_catalog_relative="/${gammabench_catalog_dir#"$gammabench_repo_dir"/}/catalog.json"
mkdir -p "$gammabench_stage/docs" "$gammabench_stage/assets"
cp -R "$gammabench_repo_dir/docs/results" "$gammabench_stage/docs/results"
cp -R "$gammabench_repo_dir/snapshots" "$gammabench_stage/snapshots"
cp -R "$gammabench_repo_dir/src/gammabench/assets/theme" "$gammabench_stage/assets/theme"
cp "$gammabench_repo_dir/src/gammabench/assets/report.css" "$gammabench_stage/assets/report.css"
cp "$gammabench_repo_dir/src/gammabench/assets/report.js" "$gammabench_stage/assets/report.js"
cp "$gammabench_repo_dir/web/workspace.js" "$gammabench_stage/assets/workspace.js"
cp "$gammabench_repo_dir/web/history.js" "$gammabench_stage/assets/history.js"
cp "$gammabench_repo_dir/web/explorer.js" "$gammabench_stage/assets/explorer.js"
cp "$gammabench_repo_dir/src/gammabench/assets/explorer.css" "$gammabench_stage/assets/explorer.css"
cp "$gammabench_repo_dir/src/gammabench/assets/metadata.json" "$gammabench_stage/assets/metadata.json"
cp "$gammabench_repo_dir/web/benchmarks.json" "$gammabench_stage/assets/benchmarks.json"
gammabench_preview_inputs=()
if [[ -f "$gammabench_repo_dir/web/preview.json" ]]; then
  gammabench_preview_inputs=(--input preview-context=preview.json)
fi
typst compile --root "$gammabench_repo_dir" --features html,bundle --format bundle \
  --creation-timestamp 0 --input "catalog-path=$gammabench_catalog_relative" \
  "${gammabench_preview_inputs[@]}" \
  "$gammabench_repo_dir/web/main.typ" "$gammabench_stage"
cp "$gammabench_repo_dir/artifacts.json" "$gammabench_stage/artifacts.json"
touch "$gammabench_stage/.nojekyll"
mv "$gammabench_stage" "$gammabench_destination"

#!/usr/bin/env bash
# Publish precomputed reports and evidence without a Python runtime or site generator.
set -euo pipefail

gammabench_repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
gammabench_destination="${1:-$gammabench_repo_dir/.gammabench/pages-site}"
gammabench_destination="$(realpath -m "$gammabench_destination")"
case "$gammabench_destination" in
  "$gammabench_repo_dir/snapshots/"*|"$gammabench_repo_dir/docs/results/"*|"$gammabench_repo_dir/web/"*)
    echo "Pages output must be outside its source evidence and reports" >&2
    exit 1
    ;;
esac
if [[ -e "$gammabench_destination" ]]; then
  echo "Pages destination already exists: $gammabench_destination" >&2
  exit 1
fi
mkdir -p "$(dirname "$gammabench_destination")"
gammabench_stage="$(mktemp -d "$(dirname "$gammabench_destination")/.pages-staging.XXXXXX")"
mkdir -p "$gammabench_stage/docs"
cp -R "$gammabench_repo_dir/docs/results" "$gammabench_stage/docs/results"
cp -R "$gammabench_repo_dir/snapshots" "$gammabench_stage/snapshots"
cp "$gammabench_repo_dir/web/index.html" "$gammabench_stage/index.html"
cp "$gammabench_repo_dir/web/404.html" "$gammabench_stage/404.html"
cp "$gammabench_repo_dir/artifacts.json" "$gammabench_stage/artifacts.json"
touch "$gammabench_stage/.nojekyll"
mv "$gammabench_stage" "$gammabench_destination"

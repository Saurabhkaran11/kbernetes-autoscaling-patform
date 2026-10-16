#!/usr/bin/env bash
# build-regression-image.sh: build a deliberately BROKEN image for Scenario D (bad release).
#
# The bug: GET /api/v1/items/{item_id} reads a field that doesn't exist, as if a refactor
# assumed a new "tags" column that was never added. Every single-item read raises KeyError
# and returns 500. Everything else still works: /health and /ready pass, so Kubernetes
# rolls the release out "successfully". Only monitoring (error-rate alert, dashboards) can
# catch it, which is the lesson of Scenario D.
#
# The bug is introduced in a TEMPORARY copy of the source, so the repository never
# contains broken code.
#
# Usage:
#   ./scripts/build-regression-image.sh            # builds items-api:v5-regression
#   kind load docker-image items-api:v5-regression --name autoscale
#
# Example of the broken behaviour:
#   GET /api/v1/items      -> 200
#   GET /api/v1/items/1    -> 500 {"detail":"Internal server error","request_id":"..."}

set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
TAG=${TAG:-items-api:v5-regression}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

cp -R "$ROOT/app" "$ROOT/requirements.txt" "$ROOT/Dockerfile" "$ROOT/.dockerignore" "$WORK/"

# Inject the bug right before the handler returns the item.
python3 - "$WORK/app/routes/items.py" <<'PY'
import sys
path = sys.argv[1]
src = open(path).read()
needle = '        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")\n    return item\n'
assert needle in src, "items.py changed; update the regression patch"
src = src.replace(needle, needle.replace("    return item\n", '    item["tag_count"] = len(item["tags"])  # REGRESSION: "tags" was never added\n    return item\n'))
open(path, "w").write(src)
PY

docker build -q -t "$TAG" --build-arg APP_VERSION=0.5.0-regression "$WORK"
echo "built $TAG (GET /api/v1/items/{item_id} now raises KeyError -> 500)"

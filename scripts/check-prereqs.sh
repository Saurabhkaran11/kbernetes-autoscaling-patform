#!/usr/bin/env bash
# check-prereqs.sh — verifies every tool this project needs is installed on macOS.
#
# Why: each later phase depends on a specific CLI (kind for the cluster, helm for
# Prometheus/Grafana, k6 for load tests, trivy for image scanning). Failing early
# with a clear list beats a confusing error halfway through Phase 4.
#
# Example:
#   $ ./scripts/check-prereqs.sh
#   [ok]      docker     Docker version 29.3.0
#   [ok]      docker daemon running
#   [missing] kind       -> brew install kind
#   ...
#   2 required tool(s) missing.      (exit code 1)

set -u

missing=0

# check <command> <install hint> <required|optional>
check() {
  local cmd="$1" hint="$2" level="$3"
  if command -v "$cmd" >/dev/null 2>&1; then
    # Most tools print their version with --version; kubectl/helm/kind use `version`.
    local ver
    ver=$("$cmd" --version 2>/dev/null | head -1)
    [ -z "$ver" ] && ver=$("$cmd" version --client 2>/dev/null | head -1)
    [ -z "$ver" ] && ver=$("$cmd" version 2>/dev/null | head -1)
    printf "[ok]      %-10s %s\n" "$cmd" "$ver"
  else
    printf "[%s] %-10s -> %s\n" "$([ "$level" = required ] && echo missing || echo 'skip   ')" "$cmd" "$hint"
    [ "$level" = required ] && missing=$((missing + 1))
  fi
}

check git     "xcode-select --install"          required
check python3 "brew install python@3.12"        required
check docker  "install Docker Desktop or OrbStack" required
check kind    "brew install kind"               required
check kubectl "brew install kubectl"            required
check helm    "brew install helm"               required
check k6      "brew install k6"                 required
check trivy   "brew install trivy"              optional   # image scanning (Phase 8)
check act     "brew install act"                optional   # run GitHub Actions locally (Phase 8)
check gh      "brew install gh"                 optional   # GitHub CLI (Phase 8)

# The docker CLI can be installed while the daemon is stopped — kind needs the daemon.
if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    echo "[ok]      docker daemon running"
  else
    echo "[missing] docker daemon not running -> open Docker Desktop (or OrbStack) and retry"
    missing=$((missing + 1))
  fi
fi

echo
if [ "$missing" -eq 0 ]; then
  echo "All required tools present."
else
  echo "$missing required tool(s) missing."
  exit 1
fi

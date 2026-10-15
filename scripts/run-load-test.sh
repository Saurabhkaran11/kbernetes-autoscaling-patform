#!/usr/bin/env bash
# run-load-test.sh: run a k6 script as a Job inside the kind cluster and record what happened.
#
# Usage:
#   ./scripts/run-load-test.sh smoke                 # 30 s, 5 VUs
#   ./scripts/run-load-test.sh ramp                  # ~10 min ramp to 60 VUs
#   PEAK_VUS=100 SETTLE=300 ./scripts/run-load-test.sh ramp
#
# What it records (also saved to load-tests/results/<test>-<timestamp>.log):
#   * an HPA/replica timeline sampled every 15 s while the test runs, plus SETTLE seconds
#     afterwards so you can see the scale-down
#   * k6's own summary: requests, throughput, error rate, latency percentiles, threshold result
#
# Example (tail of output):
#   t=135s  hpa_cpu=212%  replicas=6  ready=6
#   requests=41230  rate=68.7/s  failed=0.00%  p50=4.1ms  p95=11.8ms  p99=24.0ms
#   thresholds: PASSED (k6 exit code 0)
#
# Requires: the app deployed (k8s/overlays/local) and metrics-server installed.

set -euo pipefail

TEST=${1:?usage: run-load-test.sh <smoke|ramp>}
PEAK_VUS=${PEAK_VUS:-60}
SETTLE=${SETTLE:-0}                 # seconds to keep sampling the HPA after k6 finishes
K6_IMAGE=grafana/k6:2.3.0
JOB=k6-$TEST
ROOT=$(cd "$(dirname "$0")/.." && pwd)
RESULTS_DIR=$ROOT/load-tests/results
mkdir -p "$RESULTS_DIR"
LOG=$RESULTS_DIR/$TEST-$(date +%Y%m%d-%H%M%S).log

# Send everything to the terminal AND the results file.
exec > >(tee "$LOG") 2>&1

echo "== $TEST  peak_vus=$PEAK_VUS  started $(date -u +%Y-%m-%dT%H:%M:%SZ)"
kubectl apply -k "$ROOT/load-tests" >/dev/null
kubectl -n loadtest delete job "$JOB" --ignore-not-found --wait=true >/dev/null

# The Job runs k6, then prints its JSON summary between markers, so the numbers can be
# read from `kubectl logs` after the pod has finished. It keeps k6's exit code: non-zero
# means a threshold was crossed.
kubectl apply -f - >/dev/null <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: $JOB
  namespace: loadtest
spec:
  backoffLimit: 0                   # a load test should run once; never retry it automatically
  ttlSecondsAfterFinished: 3600     # clean up an hour after it finishes
  template:
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 12345            # the k6 image's own user
        seccompProfile: { type: RuntimeDefault }
      containers:
        - name: k6
          image: $K6_IMAGE
          env:
            - { name: PEAK_VUS, value: "$PEAK_VUS" }
          command: ["sh", "-c"]
          args:
            - >-
              k6 run --quiet --summary-export=/tmp/summary.json /scripts/$TEST.js; rc=\$?;
              echo K6_SUMMARY_BEGIN; cat /tmp/summary.json; echo; echo K6_SUMMARY_END; exit \$rc
          resources:
            requests: { cpu: 500m, memory: 128Mi }
            limits:   { cpu: "2", memory: 512Mi }
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: ["ALL"] }
          volumeMounts:
            - { name: scripts, mountPath: /scripts, readOnly: true }
            - { name: tmp, mountPath: /tmp }
      volumes:
        - name: scripts
          configMap: { name: k6-scripts }
        - name: tmp
          emptyDir: {}
EOF

sample() {
  local t=$1
  local cpu rep rdy
  cpu=$(kubectl -n autoscale-app get hpa items-api -o jsonpath='{.status.currentMetrics[0].resource.current.averageUtilization}' 2>/dev/null || true)
  rep=$(kubectl -n autoscale-app get deploy items-api -o jsonpath='{.spec.replicas}')
  rdy=$(kubectl -n autoscale-app get deploy items-api -o jsonpath='{.status.readyReplicas}')
  printf "t=%4ss  hpa_cpu=%5s  replicas=%s  ready=%s\n" "$t" "${cpu:-?}%" "$rep" "${rdy:-0}"
}

echo "-- replica timeline (sampled every 15 s)"
start=$(date +%s)
while true; do
  sample $(( $(date +%s) - start ))
  done_status=$(kubectl -n loadtest get job "$JOB" -o jsonpath='{.status.conditions[?(@.status=="True")].type}' 2>/dev/null || true)
  case "$done_status" in *Complete*|*Failed*) break ;; esac
  sleep 15
done
k6_end=$(( $(date +%s) - start ))
echo "-- k6 finished at t=${k6_end}s ($done_status)"

if [ "$SETTLE" -gt 0 ]; then
  echo "-- settling: sampling for another ${SETTLE}s to watch scale-down"
  while [ $(( $(date +%s) - start - k6_end )) -lt "$SETTLE" ]; do
    sleep 15
    sample $(( $(date +%s) - start ))
  done
fi

echo "-- k6 summary"
kubectl -n loadtest logs "job/$JOB" | sed -n '/K6_SUMMARY_BEGIN/,/K6_SUMMARY_END/p' | sed '1d;$d' > "$LOG.summary.json"
python3 - "$LOG.summary.json" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))["metrics"]
d, f, r = m["http_req_duration"], m["http_req_failed"], m["http_reqs"]
pct = lambda k: d.get(k, float("nan"))
print(f"requests={r['count']}  rate={r['rate']:.1f}/s  failed={f['value'] * 100:.2f}%  "
      f"p50={pct('med'):.1f}ms  p90={pct('p(90)'):.1f}ms  p95={pct('p(95)'):.1f}ms  max={pct('max'):.1f}ms")
checks = m.get("checks", {})
if checks:
    print(f"checks passed={checks.get('passes')}  failed={checks.get('fails')}")
PY

# Match "Complete" anywhere: since Kubernetes 1.31 a finished Job reports two True
# conditions ("SuccessCriteriaMet Complete"), so an exact string comparison would fail.
if [[ "$done_status" == *Complete* ]]; then
  echo "thresholds: PASSED (k6 exit code 0)"
else
  echo "thresholds: FAILED (k6 exited non-zero; see the threshold lines in: kubectl -n loadtest logs job/$JOB)"
fi
echo "results saved to $LOG"

#!/usr/bin/env bash
# pod-kill-drill.sh: Scenario A, "a pod unexpectedly terminates", measured under live traffic.
#
# What it does:
#   1. Starts a curl pod (in the loadtest namespace) that calls the Service ~10 times/second for DURATION seconds.
#   2. Deletes one serving pod (Running and not already terminating).
#   3. Waits for the Deployment to be back at full strength.
#   4. Reports requests per HTTP status and per pod. Status "000" = connection failed.
#
# Example:
#   $ ./scripts/pod-kill-drill.sh
#   killing items-api-6697c556f7-l2mqg
#   victim gone after 0.9s
#   requests by status:
#      2 000
#    389 200
#
# Requires: a running cluster with the app deployed (kubectl apply -k k8s/overlays/local).

set -euo pipefail

NS=autoscale-app
DURATION=${DURATION:-45}
URL=http://items-api.$NS.svc.cluster.local/api/v1/stats
OUT=$(mktemp -t drill)

# Traffic comes from the loadtest namespace: the NetworkPolicy in k8s/base allows only
# loadtest and Prometheus to reach the API (a pod in "default" would just time out).
# The namespace enforces the "restricted" Pod Security level, so the pod must declare a
# non-root user, no privilege escalation, no capabilities and the default seccomp profile.
TRAFFIC_NS=loadtest
ROOT=$(cd "$(dirname "$0")/.." && pwd)
kubectl apply -k "$ROOT/load-tests" >/dev/null
kubectl delete pod -n "$TRAFFIC_NS" traffic --ignore-not-found --wait=true >/dev/null 2>&1

# The loop the pod runs. Each output line: "<epoch> <http status> <pod that answered>"
LOOP="end=\$(( \$(date +%s) + $DURATION ))
while [ \$(date +%s) -lt \$end ]; do
  r=\$(curl -s -m 2 -w ' %{http_code}' $URL)
  echo \"\$(date +%s) \${r##* } \$(echo \"\$r\" | grep -o 'items-api-[a-z0-9-]*')\"
  sleep 0.1
done"

# The pod spec goes in as JSON (built by python, so the loop is quoted safely).
python3 - "$TRAFFIC_NS" "$LOOP" <<'PY' | kubectl apply -f - >/dev/null
import json, sys
ns, loop = sys.argv[1], sys.argv[2]
print(json.dumps({
    "apiVersion": "v1", "kind": "Pod",
    "metadata": {"name": "traffic", "namespace": ns},
    "spec": {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "securityContext": {"runAsNonRoot": True, "runAsUser": 100,
                            "seccompProfile": {"type": "RuntimeDefault"}},
        "containers": [{
            "name": "curl", "image": "curlimages/curl:8.11.1",
            "command": ["sh", "-c", loop],
            "securityContext": {"allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]}},
        }],
    },
}))
PY

kubectl wait -n "$TRAFFIC_NS" --for=condition=Ready pod/traffic --timeout=60s >/dev/null
sleep 8   # collect a baseline before the failure

victim=$(kubectl -n "$NS" get pods -l app.kubernetes.io/name=items-api -o json | python3 -c '
import json, sys
pods = json.load(sys.stdin)["items"]
print(next(p["metadata"]["name"] for p in pods
           if p["status"]["phase"] == "Running" and "deletionTimestamp" not in p["metadata"]))')

echo "killing $victim"
t0=$(python3 -c 'import time; print(time.time())')
kubectl -n "$NS" delete pod "$victim" --wait=true >/dev/null
t1=$(python3 -c 'import time; print(time.time())')
python3 -c "print(f'victim gone after {$t1 - $t0:.1f}s')"
kubectl -n "$NS" rollout status deploy/items-api --timeout=90s

kubectl wait -n "$TRAFFIC_NS" --for=jsonpath='{.status.phase}'=Succeeded pod/traffic --timeout=$((DURATION + 60))s >/dev/null
kubectl logs -n "$TRAFFIC_NS" traffic > "$OUT"
kubectl delete pod -n "$TRAFFIC_NS" traffic --wait=false >/dev/null

echo "requests by status:"; awk '{print $2}' "$OUT" | sort | uniq -c
echo "requests by pod:";    awk '{print ($3 == "" ? "(no response)" : $3)}' "$OUT" | sort | uniq -c
echo "full log: $OUT"

"""Check that the Helm chart and the Kustomize overlay produce equivalent workloads.

Names and bookkeeping labels are allowed to differ (Helm adds release labels; Kustomize
hashes the ConfigMap name). Everything that changes BEHAVIOUR must match: the container
spec, probes, resources, security contexts, rollout strategy, HPA, NetworkPolicy rules,
Service ports and the config values.

Usage (also: make helm-check):
    kubectl kustomize k8s/overlays/local > /tmp/k.yaml
    helm template items-api helm/items-api -n autoscale-app > /tmp/h.yaml
    python scripts/compare_helm_kustomize.py /tmp/k.yaml /tmp/h.yaml

Example output:
    OK   Deployment pod spec (containers, probes, resources, security)
    OK   HorizontalPodAutoscaler spec
    ...
    equivalent: 6/6 checks
"""

import copy
import json
import sys

import yaml


def load(path):
    with open(path) as f:
        return [d for d in yaml.safe_load_all(f) if d]


def one(docs, kind):
    return next(d for d in docs if d["kind"] == kind)


# Labels Helm adds to selectors that Kustomize doesn't (the release name). Every OTHER
# selector label is kept and compared, especially the namespace names in NetworkPolicies.
HELM_ONLY_SELECTOR_LABELS = {"app.kubernetes.io/instance"}


def normalise(obj):
    """Drop only what is expected to differ: object/reference names, metadata label and
    annotation sets, and Helm's release label inside selectors."""
    obj = copy.deepcopy(obj)
    if isinstance(obj, dict):
        for key in ("name", "labels", "annotations"):
            obj.pop(key, None)
        if "matchLabels" in obj:
            obj["matchLabels"] = {k: v for k, v in obj["matchLabels"].items() if k not in HELM_ONLY_SELECTOR_LABELS}
        return {k: normalise(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalise(v) for v in obj]
    return obj


def canonical(obj):
    """Key-order-independent text form, for sorting and comparing lists of specs."""
    return json.dumps(obj, sort_keys=True)


def main(kustomize_path, helm_path):
    k, h = load(kustomize_path), load(helm_path)
    kd, hd = one(k, "Deployment")["spec"], one(h, "Deployment")["spec"]
    checks = {
        "Deployment pod spec (containers, probes, resources, security)": (
            normalise(kd["template"]["spec"]),
            normalise(hd["template"]["spec"]),
        ),
        "Deployment rollout strategy and history": (
            (kd["strategy"], kd["revisionHistoryLimit"], "replicas" in kd),
            (hd["strategy"], hd["revisionHistoryLimit"], "replicas" in hd),
        ),
        "HorizontalPodAutoscaler spec": (
            normalise(one(k, "HorizontalPodAutoscaler")["spec"]),
            normalise(one(h, "HorizontalPodAutoscaler")["spec"]),
        ),
        "NetworkPolicy rules": (
            sorted(canonical(normalise(d["spec"])) for d in k if d["kind"] == "NetworkPolicy"),
            sorted(canonical(normalise(d["spec"])) for d in h if d["kind"] == "NetworkPolicy"),
        ),
        "Service type and ports": (
            (one(k, "Service")["spec"]["type"], one(k, "Service")["spec"]["ports"]),
            (one(h, "Service")["spec"]["type"], one(h, "Service")["spec"]["ports"]),
        ),
        "ConfigMap values": (one(k, "ConfigMap")["data"], one(h, "ConfigMap")["data"]),
    }
    ok = 0
    for name, (left, right) in checks.items():
        same = left == right
        ok += same
        print(f"{'OK  ' if same else 'DIFF'} {name}")
        if not same:
            print(f"     kustomize: {canonical(left)}\n     helm:      {canonical(right)}")
    print(f"equivalent: {ok}/{len(checks)} checks")
    sys.exit(0 if ok == len(checks) else 1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

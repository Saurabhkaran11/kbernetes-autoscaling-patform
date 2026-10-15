"""Turn a PrometheusRule (Kubernetes custom resource) into a plain Prometheus rule file.

Why: `promtool test rules` understands plain rule files ({"groups": [...]}), not the
Kubernetes wrapper (apiVersion/kind/metadata/spec). The groups are identical; we
just unwrap `spec`.

Example:
    $ python scripts/extract_rules.py monitoring/alerts/items-api-rules.yaml > /tmp/items-api.rules.yaml
    $ head -2 /tmp/items-api.rules.yaml
    groups:
    - name: items-api.recording
"""

import sys

import yaml


def main(path: str) -> None:
    with open(path) as f:
        doc = yaml.safe_load(f)
    if doc.get("kind") != "PrometheusRule":
        sys.exit(f"{path}: expected kind PrometheusRule, got {doc.get('kind')!r}")
    yaml.safe_dump({"groups": doc["spec"]["groups"]}, sys.stdout, sort_keys=False)


if __name__ == "__main__":
    main(sys.argv[1])

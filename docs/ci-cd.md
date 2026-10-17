# CI/CD

## 1. The pipeline (`.github/workflows/ci.yml`)

```mermaid
flowchart LR
    trig[push to main<br/>pull request<br/>manual run] --> t & a & s
    subgraph t[test]
        t1[install from<br/>requirements.lock<br/>--require-hashes] --> t2[ruff lint<br/>+ format check] --> t3[pytest<br/>JUnit + coverage<br/>≥95%] --> t4[summary table<br/>+ artifact]
    end
    subgraph a[alert-rules]
        a1[promtool check rules] --> a2[promtool test rules<br/>13 cases]
    end
    subgraph s[security]
        s1[pip-audit] --> s2[trivy secret scan] --> s3[kustomize render<br/>trivy config] --> s4[docker build] --> s5[smoke test<br/>ready + uid 10001] --> s6[trivy image<br/>fixable HIGH/CRIT]
    end
    t & a & s --> gate{all green?}
    gate -- yes --> merge[OK to merge]
    gate -- no --> fix[fix and push again]
```

| Requirement | Where |
|---|---|
| 1. Check out the repository | `actions/checkout`, pinned to a commit SHA, with `persist-credentials: false` |
| 2. Set up Python | `actions/setup-python` 3.12, with pip cache keyed on the lock files |
| 3. Install dependencies | `pip install --require-hashes --no-deps -r requirements.lock`, then dev tools |
| 4. Linting | `ruff check --output-format=github` (inline PR annotations) and `ruff format --check` |
| 5. Unit tests | `pytest` with `--cov-fail-under=95` |
| 6. Build the Docker image | `docker build` in the `security` job, then a smoke test of the built image |
| 7. Security checks | pip-audit, trivy secret / config / image ([security.md](security.md)) |
| 8. Publish test results | Markdown table on the run's Summary page, plus a `test-results` artifact (JUnit XML, coverage XML, kept 14 days) |

**Why three parallel jobs:**

- They are independent, so the pipeline takes as long as the slowest job, not the sum of all three.
- A failure shows straight away *which kind* of check broke.

**Why the test summary uses no extra action:** annotation actions need `checks: write`. A few lines of Python writing to `$GITHUB_STEP_SUMMARY` need no permissions at all.

## 2. Validating the workflow locally before pushing

All of these were run on 2026-10-15:

| Check | Command | Result |
|---|---|---|
| Workflow syntax, expressions and embedded shell | `actionlint` (with `shellcheck`) | **0 errors** |
| `test` job, exact commands, in Linux | `python:3.12-slim` container running the job's `run:` steps | hash-checked install OK; ruff clean; **62 passed, 100% coverage**; summary table rendered |
| `alert-rules` job | `make test-alerts PROMETHEUS_IMAGE=<digest-pinned>` | `SUCCESS` (8 rules, 13 cases) |
| `security` job | `make security` (same Trivy 0.76.0 and flags, Homebrew binary instead of the container image) plus the build and smoke-test steps verbatim | all 4 gates pass; smoke: ready, uid 10001 |
| Full run under `act` | `act -j test -P ubuntu-24.04=-self-hosted` | **Not completed.** See below. |

**Why `act` wasn't used for a full run:**

- `act` normally runs jobs in a Linux runner image of about 1.5 GB. This Mac had about 8 GB free, below the level where Docker's storage failed earlier (see [troubleshooting.md](troubleshooting.md)).
- `act`'s self-hosted mode avoids the image, but runs steps directly on macOS, where `actions/setup-python` fails because it expects a GitHub-hosted runner layout.
- With 20+ GB free, use:

```bash
act -j test
```

```bash
act -j alert-rules
```

```bash
act -j security
```

## 3. First run on GitHub (observed)

- **Public repository:** [`Saurabhkaran11/kbernetes-autoscaling-patform`](https://github.com/Saurabhkaran11/kbernetes-autoscaling-patform). The full history was pushed on 2026-10-17, after a secret scan of every commit.
- **Latest public run:** [#38990703832](https://github.com/Saurabhkaran11/kbernetes-autoscaling-patform/actions/runs/38990703832) on `8630ab8`, green: lint and tests 25 s, alert rules 18 s, build and security 43 s.
- **First run ever:** in the earlier private repository (`kubernetes-autoscaling-platform`, not publicly viewable), `main` at `921e58b`, run #38860472342, 2026-10-16 06:18Z.
- **Result:** **success on the first attempt**, with no workflow changes needed after the local validation. That first run's numbers:

| Job | Result | Duration | Evidence from the log |
|---|---|---|---|
| Lint and unit tests | ✅ | 19 s | `62 passed`; `Required test coverage of 95% reached. Total coverage: 100.00%` |
| Prometheus alert rule tests | ✅ | 11 s | `SUCCESS: 8 rules found`, `SUCCESS` |
| Build image and security scans | ✅ | 49 s | pip-audit `No known vulnerabilities found`; smoke `{"status":"ready"}`; Trivy image: 0 fixable HIGH/CRITICAL |

- The jobs run in parallel, so the pipeline takes about 49 s end to end.
- The `test-results` artifact (2.5 KB, JUnit and coverage XML) is kept until 2026-10-30.
- The run produced **0 annotations** (no warnings and no deprecation notices).

## 4. Re-running it on GitHub

Commits stay local by default, and each push is a deliberate step. Push to the public repository (remote `public`):

```bash
git push public main
```

```bash
gh run watch
```

To re-run the workflow without a new commit:

```bash
gh workflow run ci.yml
```

How the remotes were set up, for reference:

- `origin` is the private repository, created with `gh repo create kubernetes-autoscaling-platform --private --source . --remote origin`.
- `public` was added for the public repository:

```bash
git remote add public https://github.com/Saurabhkaran11/kbernetes-autoscaling-patform.git
```

```bash
git push public main
```

## 5. Deployment automation: designed, deliberately not built

The CI workflow never deploys. Adding CD here would be pretend:

- **GitHub can't reach the cluster.** A GitHub-hosted runner has no path to a kind cluster on a laptop.
- **Opening one would be worse.** Exposing the laptop's API server to the internet, or storing a kubeconfig as a repository secret, creates a much bigger risk than the convenience is worth.

For a real cluster, deployment would be a **separate workflow**, with these security requirements:

| Concern | Design |
|---|---|
| Separation | `deploy.yml`, separate from `ci.yml`, triggered only by a successful CI run on `main` (`workflow_run`) or a release tag. Never by pull requests from forks. |
| Image publishing | Push to GHCR with the built-in `GITHUB_TOKEN`, granting `packages: write` **only to that job**. Tag images with the commit SHA, never `latest`. |
| Supply chain | Sign the image with cosign (keyless, via OIDC), generate an SBOM, and verify the signature at admission in the cluster (Kyverno or a policy controller). |
| Cluster credentials | **No long-lived kubeconfig in secrets.** Use OIDC federation (`id-token: write`) to a cloud provider's short-lived role, scoped to one namespace with a namespaced RBAC Role. |
| Human approval | A GitHub **Environment** called `production` with required reviewers and a branch rule, so only `main` can deploy. |
| Rollout safety | Update `newTag` in the overlay (GitOps), then `kubectl rollout status` with a timeout, and run `load-tests/smoke.js` against the new version. If either fails, `kubectl rollout undo`. Better: Argo CD or Flux pulls from git, so CI never holds cluster credentials. |
| Audit | Every deploy is a commit (the tag bump) plus a workflow run linked to it. |

The **GitOps** option (Argo CD or Flux running *inside* the cluster and pulling from git) removes the biggest risk entirely: CI never needs credentials for the cluster. That is the recommended design.

# 0011. Layered CI quality gates and dependency security

## Status

Accepted (November 2026).

## Context

Local `pre-commit` hooks provide fast feedback on `git commit`, but they can be skipped
with `--no-verify`, cannot run against an ephemeral PostgreSQL service container, and
should stay fast and offline-friendly. We also need automated visibility and gating for
known vulnerabilities (CVEs) in locked Python dependencies (`uv.lock`) and GitHub Actions.

## Decision

Split quality and security checks into concentric layers:

1. **Local `pre-commit` (`.pre-commit-config.yaml`)**: Fast, offline checks on staged
   files (`ruff check --fix`, `ruff format`, `mypy`, `sqlfluff`, `gitleaks` on staged
   diffs, and file hygiene). `pytest` runs on `pre-push`.
2. **GitHub Actions CI (`.github/workflows/ci.yml`)**: Runs on every push to `main` and
   every pull request with three jobs:
   - `quality`: `make check` (`ruff`, `mypy`, `sqlfluff`) plus `uv export --no-hashes --no-emit-project | uvx pip-audit -r /dev/stdin`.
   - `test`: `pytest` against a PostgreSQL 16 service container, followed by `dbt parse`.
   - `secrets`: Full-history `gitleaks` scan.
3. **Dependabot (`.github/dependabot.yml`)**: Weekly grouped update PRs for `uv`
   (`python-dependencies`) and `github-actions`, paired with GitHub Dependabot security
   alerts.

## Consequences

**Positive**

- Local commits stay fast and work offline while CI enforces every check on a clean
  machine.
- Known vulnerabilities in `uv.lock` fail the `quality` job before merging to `main`,
  while weekly grouped Dependabot PRs keep dependencies current with minimal PR noise.

**Negative**

- `pip-audit` requires network access to query PyPI/OSV advisory databases and can fail
  unrelated PRs when a new CVE is disclosed until the affected package is upgraded.

# Snapshot integrity and calibration gate hardening

Status: resolved; externally accepted with non-blocking notes

## Issues

- [01 Snapshot usability, retry, and downstream quarantine](issues/01-snapshot-usability-and-quarantine.md) — resolved
- [02 Calibration gate and segment scope](issues/02-calibration-gate-segment-scope.md) — resolved
- [03 Provenance, ETA, and milestone reporting](issues/03-provenance-eta-and-reporting.md) — resolved

## Handoff

- Baseline: `origin/main@900fa2640bff4c6f55f2a5c0623b25580c033a0f`
- Branch: `codex/snapshot-integrity-gate-hardening`
- No commit, push, deployment, production call, or external-service write is authorized.
- Joint decision: B — 60 opens global analysis review; each segment independently needs 20.
- ChatGPT Pro initial final review: `CHANGES_REQUIRED` on three bounded invariants.
- R2 review fixed B1/B2/B3 and identified B4: a day-level sidecar could be
  borrowed by another post-policy run during a weekly rerun.
- Corrections complete: pre-policy curated sidecar precedence, post-policy
  run-local inline health, explicit 2026-08-22 health-policy cutover, and formal
  tuning-scope enforcement for unknown segments.
- Tests after B4 correction: targeted 116 passed; full offline suite 267 passed.
- ChatGPT Pro R3 final verdict: `ACCEPT_WITH_NONBLOCKING`; blocking findings:
  none. All 1–7 acceptance items are `ACCEPT` and Decision B is final.

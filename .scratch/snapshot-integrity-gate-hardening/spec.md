# Snapshot integrity and calibration gate hardening

Status: resolved

## Goal

Make the daily scanner fail closed on systemic market-data degradation, keep raw incident evidence, prevent unusable snapshots from reaching weekly or V1.3 calibration consumers, and remove ambiguity between the global calibration gate and segment-specific readiness.

## Product boundaries

- Preserve the 2026-08-18 raw artifact; quarantine is eligibility metadata, not deletion or overwrite.
- Do not change `SCAN_POOL`, selection or trade rules, `strategy_config_hash()` inputs, `ConfigHash`, or `UniverseVersion`.
- Do not tune parameters. At 60 completed-R, only an authorization review may begin. At 100, review statistical power and confidence intervals before any parameter decision.
- Any later strategy parameter change starts a new ConfigHash/cohort.
- No live market, Notion, Telegram, LLM, deployment, commit, or push operation is part of this task.

## Acceptance criteria

1. A shared health contract distinguishes ordinary eligibility exclusions from systemic data-quality failures.
2. `1 processed / 99 expected` with `97 stale_bar` and `1 insufficient_history` is blocked and unusable; `98 processed + 1 ordinary eligibility exclusion` remains a usable warning.
3. A bounded retry happens at most once and before Notion, LLM, or Telegram publication; retry cannot duplicate Telegram delivery.
4. Workflow, watchdog, weekly selection, and shadow rebuild use the same health/usability decision. Unusable days remain visible as incidents but do not enter averages or calibration.
5. The global/segment gate matches the jointly accepted repository contract and has explicit machine-readable scope plus boundary tests.
6. Runtime dependency provenance is recorded separately from strategy identity.
7. ETA arrival-rate inputs can represent normal zero-new-episode scan days; weekly reporting separates scheduled, usable, and incident days.
8. Targeted tests, the full offline unit suite, `git diff --check`, and repository-gate inventory pass or have an explicit `NOT RUN` reason.

## Evidence

ChatGPT Pro conversation and artifact intake records live under `evidence/chatgpt-pro/6a8c28ca-4e54-83e9-8dfa-913d406337b7/`.

Final verification is recorded in `evidence/verification.md`.

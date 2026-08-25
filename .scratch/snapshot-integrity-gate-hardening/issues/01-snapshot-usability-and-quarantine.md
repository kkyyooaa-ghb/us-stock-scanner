# Snapshot usability, retry, and downstream quarantine

Type: task
Status: resolved

## Acceptance criteria

- Shared reason taxonomy and systemic threshold are deterministic and tested.
- Raw degraded snapshots remain archived while operational selectors choose only usable snapshots.
- Publication occurs only after a usable final attempt; retry count is bounded to one.
- Unusable snapshots return a failing workflow signal and remain incident-visible.
- Weekly and shadow consumers exclude unusable snapshots without silently erasing the incident day.

## Comments

- 2026-08-18 is the regression fixture: 99 expected, 1 processed, 97 `stale_bar`, 1 `insufficient_history`.
- Joint default is `max(2, ceil(expected * 5%))`; NDX-99 blocks at 5.
- Raw 8/18 CSV blob remains byte-for-byte identical to `origin/main`; blocked sidecar quarantines it.
- External review B1/B2 corrections: curated sidecar now overrides stale inline
  health, and missing sidecars fail closed for dates on/after 2026-08-22.
- External review B4 correction narrows that precedence: curated sidecars are
  artifact-authoritative only before the 2026-08-22 cutover. New Actions runs
  must use their own inline health, preventing weekly rerun split-brain.

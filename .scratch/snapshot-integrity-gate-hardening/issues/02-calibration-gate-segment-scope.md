# Calibration gate and segment scope

Type: task
Status: resolved

## Acceptance criteria

- Resolve whether all three selected legs and both order types are conjunctive global blockers or independently gated segment scopes.
- Repository specs, code, report prose, JSON, and tests state the same rule.
- No tuning is authorized below the accepted global boundary or within a segment below 20 completed-R.
- No strategy setting, hash input, or scan universe changes.

## Comments

- The original measurement and weekly-integration specs currently describe an overall 60 gate plus per-segment 20 readiness.
- ChatGPT Pro and Codex jointly selected B and formally withdrew the five-segment conjunctive reading.
- Machine output separates analysis review at 60, power/CI review at 100, and never authorizes an actual parameter change in the current cohort.
- External review B3 correction: formal selected-leg/order-type scope is now
  consumed by `tuning_analysis`; unknown levels stay descriptive and can never
  become eligible hypotheses or authorized findings.

# Provenance, ETA, and milestone reporting

Type: task
Status: resolved

## Verification

- Runtime inventory records workflow, Python, platform, requirements hash, and resolved distributions outside ConfigHash.
- Corrected rebuild remains 94 episodes, 17 completed-R, 49 open, 35/52 trading days to milestones.
- Normal zero-arrival usable dates now participate in the ETA denominator; incident dates do not.

## Acceptance criteria

- Resolved runtime dependency versions are observable but do not alter strategy ConfigHash.
- ETA scan-day denominators include usable zero-new-episode days.
- Weekly outputs distinguish scheduled, usable, and incident days.
- Milestone wording distinguishes the 60 authorization-review boundary from the 100 power/CI review target.

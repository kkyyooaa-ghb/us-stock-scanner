## Agent skills

### Issue tracker

Issues are tracked as local Markdown files under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default five-role triage vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

Use a single-context domain documentation layout. See `docs/agents/domain.md`.

## GitHub synchronization

- Before editing, fetch the latest state from `origin` and check the current branch and working tree.
- Never discard local changes to resolve a divergence. Stop and report the conflict.
- After the requested work is complete and verification passes, review the diff, commit the intended source, documentation, and `.scratch/` changes, then push the current branch to GitHub.
- Never commit credentials, `.env` files, secrets, caches, or generated artifacts.
- After pushing, fetch again and verify that the local branch and its upstream have zero ahead/behind commits.

# us-stock-scanner agent guide

美股盤前掃描、交易計畫、快照健康監控、績效追蹤與週報系統。

## Start-of-task checks

- Before editing, fetch the latest state from `origin` when network access is available, then check the current branch and working tree.
- Never discard, overwrite, stage, or commit unrelated local changes. Stop and report any overlap with the requested work.
- Read relevant domain guidance from `CONTEXT.md`, `CONTEXT-MAP.md`, and `docs/adr/` when those files exist.
- For non-trivial work, use the local Markdown tracker under `.scratch/` and keep acceptance criteria and handoff notes there.

## Agent skills

### Issue tracker

Issues are tracked as local Markdown files under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default five-role triage vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

Use a single-context domain documentation layout. See `docs/agents/domain.md`.

## GitHub synchronization

- Fetching and inspecting remote state is allowed as a read-only check.
- Never commit or push without the user's explicit approval for that specific commit and push.
- Before requesting approval, show the intended file list, diff summary, verification result, proposed commit message, and target branch.
- After approval, commit only the intended source, documentation, and `.scratch/` changes, push the approved branch, fetch again, and verify zero ahead/behind against its upstream.
- Never commit credentials, `.env` files, secrets, caches, or generated artifacts.

## Claude and Codex collaboration

- Follow `docs/agents/collaboration.md` for roles, handoffs, worktree isolation, and review gates.
- Claude is the coordinator and final reviewer by default. Codex is the implementation and test agent by default.
- Only one agent may modify a worktree at a time. Use separate branches and Git worktrees for genuinely parallel implementation.
- Do not create recursive delegation loops between Claude and Codex.

## Verification

- Run the smallest relevant tests first.
- The full local suite is `python -m unittest discover -s tests -p "test_*.py"`.
- Tests must not require production credentials or write to external services. Mock network and integration boundaries where practical.
- Report the exact commands run and any tests skipped or blocked.

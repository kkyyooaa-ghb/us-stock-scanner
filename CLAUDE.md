# us-stock-scanner

@AGENTS.md

## Claude Code role

- Act as the coordinator and final reviewer unless the user explicitly assigns a different role.
- Before delegating to Codex, provide a bounded task, acceptance criteria, relevant paths, constraints, and verification commands.
- Do not modify the same worktree while a delegated Codex task is running.
- Review the resulting diff and test evidence before asking the user for commit or push approval.

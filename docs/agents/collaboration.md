# Claude and Codex Collaboration

This repository uses Claude as the coordinator and reviewer, and Codex as the implementation and test agent. The human user remains the authority for scope, destructive actions, commits, and pushes.

## Project MCP connection

The root `.mcp.json` starts Codex as a project-scoped stdio MCP server:

```text
Claude Code -> codex mcp-server -> Codex session
```

Both `claude` and `codex` must be installed, authenticated, and available on `PATH`. In Claude Code, run `claude mcp list` or `/mcp` and approve the repository's project MCP configuration when prompted. Do not store credentials in `.mcp.json`.

## Default roles

### Claude: coordinator and reviewer

- Clarify the requested outcome and identify affected areas.
- For non-trivial work, create or update `.scratch/<feature-slug>/spec.md` and its issue files.
- Define acceptance criteria, constraints, relevant paths, and verification commands before delegation.
- Delegate one bounded implementation task at a time and wait for the writing agent to finish.
- Review the diff against the spec, inspect test evidence, and request focused follow-up work when needed.

### Codex: implementer and tester

- Inspect `git status` and applicable instructions before editing.
- Preserve unrelated and pre-existing changes.
- Implement only the delegated scope, run targeted tests, then run the full relevant suite when proportionate.
- Return a concise handoff containing changed files, decisions, commands run, results, and remaining risks.
- Do not commit or push unless the user separately approves those actions.

### Human: approval authority

- Resolves material scope or product decisions.
- Approves destructive or externally visible actions.
- Reviews the proposed file list, test evidence, commit message, and branch before authorizing commit and push.

## Handoff protocol

1. Claude records a bounded task and acceptance criteria.
2. Claude calls Codex through the project MCP server with the repository root as `cwd`, `workspace-write` sandboxing, and an approval-aware policy.
3. Claude performs no writes in that worktree until Codex returns.
4. Codex implements, verifies, and returns its handoff without committing.
5. Claude reviews `git diff`, test output, and the acceptance criteria.
6. Claude either sends a focused follow-up through the same Codex thread or presents the completed change to the user.
7. Commit and push happen only after explicit user approval.

## Concurrency and worktrees

- A worktree has exactly one writing agent at a time.
- Read-only review may happen concurrently only when it cannot change files or generated state.
- For parallel implementation, create separate branches and sibling worktrees, for example `agent/codex/<slug>` and `agent/claude/<slug>`.
- Never run two implementation agents against the same branch and directory.
- Never let Claude and Codex recursively call each other; the coordinator owns delegation depth and termination.

## Verification baseline

Run targeted tests first, then use this full-suite command when appropriate:

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

Do not use production credentials during tests. Mock Telegram, Notion, market-data, and LLM integrations unless the user explicitly authorizes an external integration test.

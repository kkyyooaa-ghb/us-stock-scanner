# Claude Code compatibility note

@AGENTS.md

This file is retained only because Claude Code may load `CLAUDE.md` automatically. It does not define the repository's default collaboration model.

- The authoritative default is Codex as coordinator and final reviewer, with ChatGPT Pro as the external senior engineer. Follow `AGENTS.md` and `docs/agents/collaboration.md`.
- Claude has no default coordinator role and must not delegate to Codex through a project MCP configuration.
- If the human owner explicitly assigns Claude a bounded task, preserve the same authorization, worktree-isolation, security, evidence, and verification rules. External recommendations do not expand permissions.

# Spec: Dual-agent collaboration workflow

Status: resolved

## Goal

Replace the obsolete Claude-coordinator workflow with a durable Codex-coordinator and ChatGPT Pro external-engineer workflow while preserving human authorization, one-writer isolation, safe browser recovery, artifact verification, explicit repository gates, and source-backed research claims.

## Scope

- Update repository agent instructions and add a reusable ChatGPT Pro task template.
- Remove the obsolete project MCP delegation configuration.
- Record this resolved documentation change using the repository's local Markdown issue conventions.
- Do not change application code, tests, workflows, dependencies, data, reports, schedules, schemas, or external integrations.

## Acceptance criteria

- Roles and authority are consistent across normative instructions.
- Browser authentication remains human-only and no authentication material is handled by agents.
- Artifact manifests avoid self-referential hashes.
- Lint, typecheck, unit, contract, production build, and relevant E2E gates are explicitly inventoried.
- Version-sensitive or research claims require repository and primary or authoritative sources, with facts separated from inference.

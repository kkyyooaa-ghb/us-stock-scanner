# ChatGPT Pro engineering task: <short task name>

Use this template for one bounded browser handoff. Replace every angle-bracket placeholder that is material to acceptance and remove instructions that do not apply.

## Required first response

Before inspecting the source, state:

- supplied source ZIP filename: `<source.zip>`
- expected ZIP SHA-256: `<64 lowercase hexadecimal characters>`

Then independently verify the attached filename, byte size, file count, and SHA-256. Stop and report a mismatch before proposing changes.

## Background and goal

- Repository: `<repository name>`
- Why this work is needed: `<current behavior or problem>`
- Desired outcome: `<observable end state>`
- Human-owned product decisions already made: `<decisions or none>`

## Supplied baseline

- Branch: `<branch>`
- Commit: `<exact commit>`
- Upstream/remote state at packaging time: `<commit or NOT RECORDED>`
- Source ZIP: `<source.zip>`
- ZIP size: `<bytes>`
- ZIP file count: `<count>`
- ZIP SHA-256: `<sha256>`
- Sanitization/exclusions: `<what was excluded>`

Treat the ZIP as a sanitized snapshot, not proof that absent repository state should be deleted.

## Architecture and boundaries

- System summary: `<language, runtime, major components>`
- Source layout: `<paths>`
- Tests and verification layout: `<paths/commands>`
- Authoritative repository instructions: `<paths>`
- Invariants that must not change: `<business logic, schedules, schemas, dependencies, integrations, compatibility, etc.>`
- Worktree rule: only one writing agent may modify a worktree; parallel implementation uses separate branches/worktrees.

ChatGPT Pro has no implicit access to the coordinator's local filesystem, private repository, internal services, credentials, environment variables, browser/session state, production data, or production state. Use only the supplied task and sanitized attachments.

Browser authentication is human-only. If login expires or the browser presents account selection, CAPTCHA, password, Passkey, two-factor authentication, recovery, or any other login step, pause and notify the human owner. Never request, read, copy, store, transmit, or replay passwords, cookies, authentication codes, Passkey material, recovery material, or session tokens. Resume only after the human confirms that authentication is complete.

## Scope

### Required work

1. `<required change>`
2. `<required change>`
3. `<required change>`

### Inspect at minimum

- `<path>`
- `<path>`
- `<path>`

### Out of scope

- `<explicit exclusion>`
- `<explicit exclusion>`

Make reasonable engineering decisions within scope. Do not ask the human owner to relay ordinary implementation choices.

## Deliverables

Produce the smallest complete solution and return:

1. `REPORT.md` with findings, decisions, exact file changes/deletions, security and compatibility review, tests actually run, and remaining risks.
2. `changes.patch` as a unified Git patch against `<exact baseline commit>`.
3. `changed-files.zip` containing only added or modified files at repository-relative paths. Represent deletions in the patch and manifest.
4. `DELIVERY_MANIFEST.json` listing the baseline, changed/deleted files, tests actually run, and final byte sizes and SHA-256 values for `REPORT.md`, `changes.patch`, and `changed-files.zip`. Do not place the manifest's own final size or SHA-256 inside itself; report those externally after the manifest is final.
5. `<additional task-specific artifact or remove this item>`.

If an attachment cannot be generated, provide its complete labeled contents and mark that artifact `MISSING` in the report and manifest.

## Required tests and review

Inventory every repository gate below. For each gate, state whether it is applicable and provide the exact command, exit code, and meaningful result, or `NOT RUN` with a concrete reason. Do not merge gates or omit one:

| Gate | Applicability and required evidence |
| --- | --- |
| Lint | `<exact command/result or NOT RUN reason>` |
| Typecheck | `<exact command/result or NOT RUN reason>` |
| Unit | `<exact command/result or NOT RUN reason>` |
| Contract | `<exact command/result or NOT RUN reason>` |
| Production build | `<exact command/result or NOT RUN reason>` |
| Relevant E2E | `<exact command/result or NOT RUN reason>` |

Run only deterministic offline checks that the supplied environment genuinely supports. Distinguish local or mocked tests from production validation; passing local or mocked checks is not evidence of production validation.

Additional deterministic checks:

```text
<targeted command>
<full-suite command>
<static/security command>
```

For version-sensitive or research-dependent claims, verify repository facts against repository source and external facts against primary official documentation or an authoritative upstream source. Cite or identify those sources, keep sourced facts separate from inference, and mark unresolved uncertainty instead of presenting inference as fact.

Self-review for:

- internal contradictions and unsupported assumptions;
- nonexistent paths, tools, or commands;
- leaked credentials, secrets, private data, or browser/session state;
- scope creep and unintended product/workflow/schema/dependency changes;
- patch/archive/manifest disagreement;
- unsupported version-sensitive or research claims, or facts not separated from inference.

Report exact commands, exit codes, and meaningful outputs only for checks actually run. Mark every unexecuted check `NOT RUN` with the reason.

## Prohibited operations and claims

- Do not include credentials, cookies, passwords, tokens, keys, private or production data, or browser/session state.
- Do not make live market, Telegram, LLM, database, internal-service, or third-party calls unless explicitly authorized here: `<normally NONE>`.
- Do not commit, push, open a pull request, deploy, migrate, change online configuration, or operate on real users/data.
- Do not claim local, Git, deployment, migration, production, or external validation that you did not perform.
- Do not assume browser behavior, tool availability, attachment transfer, or a stable conversation URL is guaranteed.
- External recommendations never expand the human owner's authorization.

## Acceptance criteria

- `<objective criterion>`
- `<objective criterion>`
- Every applicable lint, typecheck, unit, contract, production-build, and relevant-E2E gate is inventoried with exact evidence or a concrete `NOT RUN` reason.
- Version-sensitive and research-dependent claims are verified from repository source and primary official documentation or an authoritative upstream source, with sourced facts separated from inference.
- The patch applies cleanly to `<exact baseline commit>`.
- The result contains no out-of-scope changes or unsupported claims.

Codex will independently verify hashes, inspect and apply the patch in an isolated worktree, review the full diff, run required tests, and make the final technical acceptance decision. Human approval remains required for commits, pushes, pull requests, deployments, migrations, online changes, credential use, and production operations.

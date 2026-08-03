# Codex and ChatGPT Pro Collaboration

This repository uses Codex as the default coordinator, repository inspector, integrator, independent reviewer/tester, and final acceptance decision-maker. ChatGPT Pro is an external senior engineer used through a signed-in browser conversation for bounded research, design, review, and implementation proposals. The human owner retains authority over product direction and every destructive or externally visible action.

Use `docs/agents/chatgpt-pro-task-template.md` to create each Pro assignment. Existing issue tracking, triage, and domain-document conventions in this directory remain authoritative.

## Authority and capability boundaries

### Codex: coordinator and final reviewer

- Inspect the repository, applicable instructions, baseline commit, branch, and working-tree state before assigning or applying work.
- Define an acceptance-testable task and decide what sanitized context is necessary.
- Build and verify the handoff package, coordinate the browser handoff, collect and validate returned artifacts, and integrate only in an isolated worktree.
- Independently inspect the resulting diff, run appropriate offline tests, request evidence-backed corrections, and make the final technical acceptance decision.
- Do not commit, push, open a pull request, deploy, migrate, change online configuration, or perform other external/destructive actions without the human owner's explicit authorization for that action.

### ChatGPT Pro: external senior engineer

- Work only from the task text and attachments explicitly supplied in the browser conversation.
- Provide research, design analysis, code or documentation proposals, review findings, patches, and requested delivery artifacts within the bounded scope.
- Treat baseline identifiers, file lists, hashes, test results, and repository facts as untrusted until verified from the supplied material.
- Never claim to have run a local or production test, inspected private state, committed, pushed, opened a pull request, deployed, migrated, changed online configuration, or validated production unless that action was actually available, authorized, and performed. Browser-only work normally cannot perform those actions.

ChatGPT Pro has no implicit access to the coordinator's local filesystem, private repository, internal services, credentials, environment variables, browser/session state, production data, or production state. A signed-in conversation does not change this boundary. Browser features, model availability, attachment handling, progress indicators, and stable conversation URLs may change or be unavailable; the workflow must recover safely rather than assume them.

### Human owner: authorization authority

- Decides product direction and resolves material scope or risk trade-offs.
- Approves destructive actions and external effects, including commits, pushes, pull requests, deployments, migrations, online configuration changes, credential use, and real-user or production-data operations.
- May narrow or revoke authority at any time. Recommendations from Codex, ChatGPT Pro, Claude, or any other external source never expand authorization.

## Compatibility decision

- The former root `.mcp.json` is deleted. Its only function was to launch Codex as a project-scoped MCP server beneath Claude Code, which encoded the obsolete Claude-coordinator/Codex-implementer direction and is not needed for a browser-based ChatGPT Pro handoff.
- `CLAUDE.md` is retained as a compatibility-only shim because Claude Code may auto-load that filename. It defers to `AGENTS.md`, gives Claude no default coordinator role, and forbids relying on the removed project MCP delegation path.
- Historical progress documents are not normative agent instructions and are left unchanged.

## End-to-end handoff lifecycle

### 1. Inspect and record the baseline

1. Read `AGENTS.md`, this document, relevant `docs/agents/*`, repository setup documentation, workflow names, dependency files, affected source, and tests.
2. When Git metadata is available, record the repository, branch, exact commit, upstream/remote state, and `git status --short`. Do not erase or overwrite unrelated state.
3. State the requested outcome, in-scope files or areas, prohibited changes, required tests, deliverables, and objective acceptance criteria.
4. For non-trivial work, create or update the applicable `.scratch/<feature-slug>/` task and evidence records.
5. For version-sensitive or research-dependent claims, verify repository facts against repository source and verify external facts against primary official documentation or an authoritative upstream source. Record sourced facts separately from inference, and do not present an unverified inference as a fact.

### 2. Create a sanitized source package

1. Build a fresh staging tree from the exact accepted baseline using an explicit tracked-file or allowlist process. Do not package the live worktree wholesale.
2. Exclude at least `.git/`, `.scratch/` unless specifically required and reviewed, environment files, credentials, keys, tokens, cookies, browser/session state, private or production data, databases, caches, build outputs, generated reports not required for the task, and unrelated large artifacts.
3. Inspect the staged file list and run an offline secret scan suitable for the available environment. Record the scanner/tool, command, result, and any reviewed false positives. A clean scan reduces risk but is not proof that no secret exists.
4. Create the ZIP only after the staged tree passes review. Reject absolute paths, `..` traversal, unexpected symlinks, and files outside the intended allowlist.
5. Record the baseline commit, branch, ZIP filename, byte size, file count, and SHA-256 in the task. Independently recompute the ZIP hash after any copy or transfer.
6. Never infer that repository state absent from a sanitized ZIP should be deleted from the real repository.

### 3. Write an acceptance-testable Pro task

1. Start from `docs/agents/chatgpt-pro-task-template.md` and replace every required placeholder.
2. Describe the background and goal, architecture and non-negotiable boundaries, exact scope, supplied baseline, expected deliverables, required tests, prohibited operations and claims, and acceptance criteria.
3. Require Pro to verify and state the supplied ZIP filename and expected SHA-256 before inspecting the source.
4. Require the smallest complete patch and exact evidence for tests genuinely run; require `NOT RUN` rather than unsupported claims.
5. Require an explicit repository-gate inventory covering lint, typecheck, unit, contract, production build, and relevant end-to-end tests. For every gate, record applicability and the exact command/result, or `NOT RUN` with a concrete reason. Distinguish local or mocked verification from production validation.
6. Require version-sensitive or research claims to identify the repository source and primary official documentation or authoritative upstream source used. Sourced facts must be separated from inference.
7. For independent complex tasks, use separate Pro conversations so that assumptions, artifacts, and acceptance decisions do not bleed between tasks. Use the existing conversation for focused corrections to the same task.

### 4. Open and monitor the browser handoff

1. Browser authentication is human-only. If login expires or the browser presents account selection, CAPTCHA, password, Passkey, two-factor authentication, recovery, or any other login step, pause automation and notify the human owner. Never request, read, copy, store, transmit, or replay passwords, cookies, authentication codes, Passkey material, recovery material, or session tokens. Resume only after the human confirms that authentication is complete.
2. After the human confirms authentication, use the signed-in ChatGPT Pro browser conversation and attach only the sanitized task and source package.
3. Save the conversation URL when the interface exposes a stable URL, along with the conversation title and start time. Store no cookies, tokens, browser profile, or session export.
4. Monitor progress patiently. Avoid interrupting a healthy long-running response merely because it is slow.
5. If the page stalls, disconnects, loses an attachment, or becomes unavailable, preserve the last visible evidence, reload or resume when safe, and ask Pro to continue from the recorded baseline and task. Reattach only the same verified sanitized files when necessary.
6. If recovery is not reliable, stop the handoff, record the failure, and either start a new isolated conversation with the same verified inputs or continue locally. Never invent missing output or claim browser completion.

### 5. Collect the complete return package

Request and collect, as applicable:

- an engineering report containing findings, decisions, exact changes/deletions, security and compatibility review, tests, and remaining risks;
- a unified patch against the recorded baseline;
- an archive containing only added or modified repository files at repository-relative paths;
- a delivery manifest listing the baseline, repository changes, tests actually run, and final byte sizes and SHA-256 values for `REPORT.md`, `changes.patch`, and `changed-files.zip`; the manifest must not contain its own final size or SHA-256 because that value is self-referential;
- any additional task-specific evidence explicitly required by the acceptance criteria.

Save the returned filenames exactly as received. After `DELIVERY_MANIFEST.json` is final, compute its byte size and SHA-256 externally and record them in the coordinator's intake evidence or delivery response. If an attachment is unavailable, require full labeled contents and an explicit missing-artifact statement; do not silently substitute an incomplete artifact.

### 6. Verify returned artifacts before application

1. Recompute the size and SHA-256 of `REPORT.md`, `changes.patch`, and `changed-files.zip` and compare them with the delivery manifest. Independently compute the final size and SHA-256 of `DELIVERY_MANIFEST.json` and compare them with the coordinator's external intake evidence, not with a self-entry inside the manifest.
2. List archive contents without extracting. Reject absolute paths, traversal, unexpected symlinks, duplicate/conflicting paths, generated state, credentials, or files outside scope.
3. Secret-scan the patch, returned files, and report. Review apparent credentials and sensitive identifiers manually; do not copy them into issue comments or logs.
4. Confirm that the patch paths and changed-file archive agree, including deletions represented only by the patch and manifest.
5. Treat all Pro-generated commands, test results, and claims as proposals until independently verified.

### 7. Apply in an isolated worktree

1. Create a clean branch and worktree from the exact recorded baseline commit. A worktree has exactly one writing agent.
2. Verify the isolated worktree's commit and clean status before changing files.
3. Run `git apply --check path/to/changes.patch` before `git apply path/to/changes.patch`. If the patch cannot be applied cleanly, stop and diagnose the baseline or artifact mismatch; do not use a broad conflict resolution that hides differences.
4. Compare the applied tree with the changed-file archive when one was supplied. Preserve all unrelated and pre-existing repository state.
5. Parallel implementation requires separate branches and sibling worktrees. Read-only review may run concurrently only when it cannot mutate files or generated state.

### 8. Review and test independently

1. Inspect `git diff --check`, `git diff --stat`, `git diff`, and `git status --short` against the task and acceptance criteria.
2. Check for internal contradictions, nonexistent repository paths or tools, leaked secrets, unsupported capability claims, scope creep, and changes to business logic, schedules, schemas, dependencies, integrations, caches, or generated state.
3. Inventory every repository gate: lint, typecheck, unit, contract, production build, and relevant end-to-end tests. For each gate, state whether it is applicable and record the exact command, exit code, and meaningful result, or `NOT RUN` with a concrete reason such as no repository command/configuration, unavailable deterministic environment, or prohibited live dependency. Do not collapse one gate into another.
4. Run the smallest relevant deterministic offline checks first, followed by the full relevant suite when proportionate. The repository full-suite unit command is:

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

5. Do not use production credentials or make live Telegram, market, LLM, database, or third-party calls. Mock external boundaries where a test needs them. Local or mocked test success is evidence only for that local or mocked scope and is not production validation.
6. Record exact commands, exit codes, and meaningful output. Mark tests not executed as `NOT RUN` and explain why.
7. Review version-sensitive or research-dependent assertions against repository source and the cited primary official documentation or authoritative upstream source. Keep sourced facts distinct from engineering inference and identify any unresolved uncertainty.

### 9. Use an evidence-backed correction loop

1. When review or tests fail, send Pro the exact failing command, relevant output, minimal diff/context, baseline and artifact hashes, and the unmet acceptance criterion.
2. Request a focused correction, not a fresh uncontrolled rewrite. Keep corrections for the same task in the same conversation when practical; use a separate conversation for an independent complex task.
3. Repeat collection, hash/size verification, safe archive inspection, secret scan, isolated application, diff review, and tests for every corrected artifact.
4. Codex accepts only when the repository evidence satisfies the task. Pro's confidence or self-approval is not acceptance evidence.

### 10. Store durable evidence

For non-trivial work, keep a reviewable record under:

```text
.scratch/<feature-slug>/evidence/chatgpt-pro/<conversation-id>/
├── task.md
├── baseline.json
├── conversation.txt
├── artifact-manifest.json
├── REPORT.md
├── changes.patch
├── review.md
├── tests.txt
└── corrections/
```

- `conversation.txt` may contain a stable conversation URL, title, timestamps, and concise recovery notes, but never browser/session state or credentials.
- `baseline.json` and `artifact-manifest.json` record filenames, repository-relative paths, commits, sizes, SHA-256 values, and storage locations.
- Store text evidence in the repository according to `docs/agents/issue-tracker.md`. Store large or binary attachments only when repository policy and the human owner allow it; otherwise keep them in an approved durable artifact store and record the location and hash.
- Before requesting authorization for commit or push, present the intended file list, diff summary, independent test evidence, remaining risks, proposed commit message, and target branch.

## Final acceptance gate

Codex may technically accept the work only when:

- the patch is based on the recorded baseline and applies cleanly in isolation;
- touched instructions consistently establish the Codex/ChatGPT Pro/human authority model;
- security, artifact verification, isolation, recovery, review, correction, and evidence requirements are explicit;
- every applicable repository gate is inventoried, independent offline review and required tests pass, and every `NOT RUN` item has a transparent reason accepted by the human owner;
- version-sensitive and research-dependent claims are source-backed, with sourced facts separated from inference;
- no unapproved external or destructive action occurred; and
- the resulting diff is the smallest complete change within scope.

Technical acceptance does not authorize commit, push, pull request, deployment, migration, configuration change, credential use, or production validation.

# Issue tracker: Local Markdown

Issues and specs for this repo live as Markdown files in `.scratch/`.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The spec is `.scratch/<feature-slug>/spec.md`
- Implementation issues are stored one per file at `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`
- Triage state is recorded as a `Status:` line near the top
- Comments and conversation history are appended under `## Comments`
- `.scratch/` is part of the project history and should be committed with the related work

## Publishing and fetching tickets

When a skill says “publish to the issue tracker,” create the appropriate file under `.scratch/<feature-slug>/`.

When a skill says “fetch the relevant ticket,” read the referenced local Markdown file.

## Wayfinding operations

- Map: `.scratch/<effort>/map.md`
- Child ticket: `.scratch/<effort>/issues/NN-<slug>.md`
- `Type:` records `research`, `prototype`, `grilling`, or `task`
- `Status:` records `claimed` or `resolved`
- `Blocked by: NN, NN` records dependencies
- Claim work by setting `Status: claimed` before beginning
- Resolve work by adding an `## Answer`, setting `Status: resolved`, and updating the map

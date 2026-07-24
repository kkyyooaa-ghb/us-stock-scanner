# Domain Docs

## Before exploring, read these

- `CONTEXT.md` at the repository root
- `CONTEXT-MAP.md` if present
- Relevant ADRs under `docs/adr/`
- Context-specific ADRs under `src/<context>/docs/adr/`, if present

If these files do not exist, proceed silently. Domain-modeling skills create them when terminology or architectural decisions are resolved.

## File structure

This repository uses the single-context layout:

```text
/
├── CONTEXT.md
├── docs/
│   └── adr/
└── src/
```

## Vocabulary and ADRs

Use domain terms as defined in `CONTEXT.md`. Avoid synonyms that the glossary excludes.

If proposed work contradicts an existing ADR, state the conflict explicitly instead of silently overriding it.

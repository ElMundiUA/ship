# Migration guide — instruction-first transition (v0.6)

This migration formalizes Ship as a methodology/prompt package instead of a bundled runtime/CLI distribution.

## Breaking change summary

- Legacy `runtime/` directory and bundled CLI implementation are removed from the repository.
- Tool-specific setup instructions are replaced by interactive, vendor-neutral adaptation contracts.
- The default user path is now **Getting started -> copy-paste to agent**.

## Migration stages

### Stage 1 — Information architecture

- Introduce `Getting started` as the primary operational entry.
- Reframe `Framework` as `The book` (rationale and motivation).
- Keep adoption, tools, and examples as supporting tabs.

### Stage 2 — Runtime/tooling removal

- Remove legacy runtime sources and prebuilt CLI artifacts from this repo.
- Move repository-internal docs deployment helpers to `scripts/`.
- Update docs and workflow references accordingly.

### Stage 3 — Interface-first adoption

- Add `Agent setup contract` for interactive discovery.
- Rewrite onboarding prompts to require interview-first behavior.
- Replace tracker adapter docs with vendor-neutral adaptation contract.

### Stage 4 — Reference library growth

- Keep ElMundi as battle-tested reference implementation.
- Add contribution guide for external reference setups.

## Compatibility notes

- Existing downstream repos that depended on old runtime paths must keep their own runtime layer or migrate to stack-native implementations.
- Historical docs in archive/localized pages may still mention legacy filenames for context; treat them as historical references unless explicitly marked current.

## Verification checklist

- [x] Manual builds on the Next site (`npm run landing:build`) after migration.
- [x] Navigation includes Getting started, The book, setup contract, and reference contribution guide.
- [x] No active workflow in this repo depends on removed `runtime/` paths.

## Operator action after upgrade

1. Re-read `Getting started` and `Agent setup contract`.
2. Re-run adoption in target repos with interactive discovery.
3. Confirm daily digest + retro recipients (DLs) and release gates.

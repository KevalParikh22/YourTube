# HANDOFF

Read this first in every new session, whatever the tool. The orchestrator keeps it updated.

**Last updated:** 2026-10-28 by claude
**Branch:** feat/phase-3-brain
**Active plan:** docs/plan/phase-3-brain.md (done)

## Done
- Phase 3 (the brain): summaries on click, related moments, learn more, knowledge pages (feat/phase-3-brain, reviewed, not merged or pushed yet).
- Phases 0, 1, 2, 2.5 and Libraries are complete (see CLAUDE.md, docs/PRD-*.md).
- Manual tests §1 (library at real size) passed on 2026-10-24.

## In progress
- Nothing.

## Next up
- User: manual test session S7 (Phase 3), then S1-S6.
- Merge feat/phase-3-brain into master and push, when the user agrees.
- User's list of UI/layout issues -> a small plan.

## Problems / blockers
- None.

## Worker quota notes
- Phase 3: agy finished T4 on its own; on T2 it hit the 20-min TIMEOUT_SECS and codex took over.
- 2026-10-27: Gemini CLI no longer serves personal/Pro accounts (Google moved them to Antigravity CLI). New worker `agy` (Antigravity CLI 1.4.8, Google AI Pro, model gemini-3.8-flash-high) is first in the build and ask chains. It runs `--mode accept-edits --sandbox`; shell commands are limited by an allow-list in ~/.gemini/antigravity-cli/settings.json (unit tests, read-only git, grep/ls/cat/head/tail/wc/sed -n). Any other command ends its run, so team.sh falls back to codex; widen the list only with read-only commands. The old `gemini` worker still uses the free API key.
- Gemini (free key, gemini-2.5-flash) hits its 250k input-tokens/minute limit on any task touching web/index.html; codex did T2-T7.
- team.conf fixes 2026-10-24: codex `--sandbox workspace-write` (no more --full-auto); gemini `--skip-trust`.

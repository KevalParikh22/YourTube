# HANDOFF

Read this first in every new session, whatever the tool. The orchestrator keeps it updated.

**Last updated:** 2026-10-27 by claude
**Branch:** feat/phase-3-brain
**Active plan:** docs/plan/phase-3-brain.md (not started)

## Done
- Phases 0, 1, 2, 2.5 and Libraries are complete (see CLAUDE.md, docs/PRD-*.md).
- Manual tests §1 (library at real size) passed on 2026-10-24.

## In progress
- Nothing. Feedback round 2 (docs/plan/feedback-round-2.md): T1-T8 done, branch reviewed by codex; its one real finding (formula weights display) fixed.

## Next up
- User runs manual test sessions S1-S6 (docs/MANUAL-TESTS.md), starting with S1 (new ratings).
- Phase 3 (better Ask answers) once the user decides.
- Push: needs a remote URL from the user, and a decision on rewriting the commit email first.

## Problems / blockers
- No git remote yet.

## Worker quota notes
- 2026-10-27: Gemini CLI no longer serves personal/Pro accounts (Google moved them to Antigravity CLI). New worker `agy` (Antigravity CLI 1.4.8, Google AI Pro, model gemini-3.8-flash-high) is first in the build and ask chains. It runs `--mode accept-edits --sandbox`; shell commands are limited by an allow-list in ~/.gemini/antigravity-cli/settings.json (unit tests, read-only git, grep/ls/cat/head/tail/wc/sed -n). Any other command ends its run, so team.sh falls back to codex; widen the list only with read-only commands. The old `gemini` worker still uses the free API key.
- Gemini (free key, gemini-2.5-flash) hits its 250k input-tokens/minute limit on any task touching web/index.html; codex did T2-T7.
- team.conf fixes 2026-10-24: codex `--sandbox workspace-write` (no more --full-auto); gemini `--skip-trust`.

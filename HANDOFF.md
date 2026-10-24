# HANDOFF

Read this first in every new session, whatever the tool. The orchestrator keeps it updated.

**Last updated:** 2026-10-24 by claude
**Branch:** feat/feedback-round-2 (checkpoint tag before it: checkpoint-before-feedback-2)
**Active plan:** docs/plan/feedback-round-2.md

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
- Gemini (free key, gemini-2.5-flash) hits its 250k input-tokens/minute limit on any task touching web/index.html; codex did T2-T7.
- team.conf fixes 2026-10-24: codex `--sandbox workspace-write` (no more --full-auto); gemini `--skip-trust`.

# HANDOFF

Read this first in every new session, whatever the tool. The orchestrator keeps it updated.

**Last updated:** 2026-10-28 by claude
**Branch:** feat/reliability
**Active plan:** docs/plan/reliability.md (planned, not started)

## Done
- Navigation redesign (T1-T7): top bar keeps the six main pages with Search, Settings and Help icons; floating Ask pop-up; Settings sidebar with seven sections (Cloud AI and Local AI separate); separate Help page; About-you validation and language dropdown (`llm.LANGUAGES`, server-checked); keyboard shortcuts with `?` overlay and skip link; inline Add category saved protected. Docs and manual session S8 added. Review (codex) found 2 issues, both fixed. Merged into master and pushed 2026-10-29.
- Public release readiness: MIT licence, disclaimer, SECURITY.md, per-OS notes, always-on dashboard access token, GitHub Actions CI (passing). The repo is still private; the user decides when to make it public.
- page-load: old static report deleted (-364 lines); visual_hints phrase scan cached (library load 0.195 s -> 0.07 s); index_version now uses a change counter (meta table), fixing stale caches when transcript text is replaced with the same number of pieces.
- Phase 3 (the brain): summaries on click, related moments, learn more, knowledge pages (feat/phase-3-brain, reviewed, not merged or pushed yet).
- Phases 0, 1, 2, 2.5 and Libraries are complete (see CLAUDE.md, docs/PRD-*.md).
- Manual tests §1 (library at real size) passed on 2026-10-24.

## In progress
- Planned (not started): reliability (T1-T6): log file with redaction, cause/fix for failures, stored access per playlist + Try again/Dismiss, start at login (systemd/LaunchAgent), untrack AI/team files.

## Next up
- User: manual test sessions (S7, S8 first), list of UI/layout issues, pick the next feature, decide on write-back.
- The dashboard needs its access link: `.venv/bin/python dashboard.py --print-url` (open it once per browser).

## Problems / blockers
- None.

## Worker quota notes
- nav-redesign: codex sandbox blocks localhost sockets, so its own unittest runs fail there; team.sh verify (outside the sandbox) is the real check. A second test dashboard on another port overwrites the `ytkb_token` cookie (cookies are per host, not per port); reopen the real `--print-url` link afterwards.
- agy cannot delete files (not on its allow-list); give file deletions to codex or do them yourself.
- Phase 3: agy finished T4 on its own; on T2 it hit the 20-min TIMEOUT_SECS and codex took over.
- 2026-10-27: Gemini CLI no longer serves personal/Pro accounts (Google moved them to Antigravity CLI). New worker `agy` (Antigravity CLI 1.4.8, Google AI Pro, model gemini-3.8-flash-high) is first in the build and ask chains. It runs `--mode accept-edits --sandbox`; shell commands are limited by an allow-list in ~/.gemini/antigravity-cli/settings.json (unit tests, read-only git, grep/ls/cat/head/tail/wc/sed -n). Any other command ends its run, so team.sh falls back to codex; widen the list only with read-only commands. The old `gemini` worker still uses the free API key.
- Gemini (free key, gemini-2.5-flash) hits its 250k input-tokens/minute limit on any task touching web/index.html; codex did T2-T7.
- team.conf fixes 2026-10-24: codex `--sandbox workspace-write` (no more --full-auto); gemini `--skip-trust`.

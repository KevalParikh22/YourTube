---
description: Claude plans a feature into small tasks for the worker LLMs (writes docs/plan/<slug>.md)
argument-hint: <what to build>
disable-model-invocation: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(.team/team.sh ask:*), Bash(git status), Bash(git switch -c:*), Bash(git branch:*)
---

You are the orchestrator (see `.team/ORCHESTRATOR.md`). Plan this feature: **$ARGUMENTS**

Use as few of your own tokens as you can:
1. Read `HANDOFF.md` and `docs/architecture.md`. If architecture.md has no real content, don't explore the repo yourself. Delegate it (run in background, then read the saved file):
   `.team/team.sh ask "Map this codebase for other engineers: purpose, folder layout, main data flow, key modules and their public functions, how to run and test. Max 150 lines." --save docs/architecture.md`
2. If you need specific facts (where X is implemented, how Y is called), use `.team/team.sh ask "..."` rather than reading many files. Open individual files only for the decisions that need them.
3. Copy `docs/plan/_TEMPLATE.md` to `docs/plan/<feature-slug>.md` and fill it in:
   - Concrete **Design notes**: names, types, function signatures, API shapes, file locations. Workers must not have to make design decisions.
   - Tasks `T1…Tn`, each about 1–3 files and under ~200 lines, in dependency order, with steps, acceptance criteria and a verify command.
   - `worker:` gemini for most tasks, codex only for small focused fixes, **claude** for anything security-sensitive or needing deep judgement.
4. If on main/master, create the branch named in the plan (`git switch -c feat/<slug>`).
5. Update `HANDOFF.md` (active plan, next up).

Finally, show the user the task list (id, title, worker) and ask if they want to start with `/team-run docs/plan/<slug>.md` or build tasks one at a time with `/team-build`.

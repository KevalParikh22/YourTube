---
description: Run a whole plan, task by task, with worker LLMs, then review the branch
argument-hint: <plan-file> [from-task-id]
disable-model-invocation: true
allowed-tools: Read, Edit, Bash(.team/team.sh:*), Bash(git status:*), Bash(git diff:*), Bash(git add:*), Bash(git commit:*), Bash(git log:*), Bash(git stash list), Bash(git switch -c:*)
---

You are the orchestrator (see `.team/ORCHESTRATOR.md`). Execute the plan `$ARGUMENTS` from start to finish (or from the given task id).

For each task with `status: todo`, in dependency order, follow exactly the procedure in `.claude/commands/team-build.md` (delegate with `run_in_background: true`, verify the diff yourself, commit, retry once, then take over).

Rules while running:
- Keep your context small. Don't paste worker logs into the conversation. Read only the summary, the report's RESULT block, and `git diff` of the task.
- After each task, give the user one line: `T3 ✓ gemini · verify PASS · a1b2c3d`.
- **Stop and ask the user** if a task needs a design decision the plan doesn't cover, if a worker changes files outside its scope twice, or if verify keeps failing for reasons outside the task.
- If every worker is out of quota (exit 3) on two tasks in a row, stop, update `HANDOFF.md`, and tell the user which tasks remain.

When all tasks are done:
1. `.team/team.sh review --plan <plan>` (in background). Triage the findings yourself, fix the real ones (small fixes yourself, bigger ones via `team.sh build --prompt-file`), and commit.
2. Final `.team/team.sh verify`.
3. Update `HANDOFF.md` and give the user a summary: tasks done by each worker, review verdict, anything left over.

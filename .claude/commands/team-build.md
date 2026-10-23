---
description: Delegate one plan task to a worker LLM (fallback gemini → kimi → codex), then verify and commit
argument-hint: <plan-file> <task-id> [worker]
disable-model-invocation: true
allowed-tools: Read, Edit, Bash(.team/team.sh:*), Bash(git status:*), Bash(git diff:*), Bash(git add:*), Bash(git commit:*), Bash(git stash list), Bash(git switch -c:*)
---

You are the orchestrator (see `.team/ORCHESTRATOR.md`). Arguments: `$ARGUMENTS` = plan file, task id, optional preferred worker.

1. Read the task in the plan file. If its `worker:` is `claude`, do it yourself and skip to step 4.
2. Make sure the tree is clean and you're not on main (commit or ask the user if not). Then run, **with Bash `run_in_background: true`**:
   `.team/team.sh build --plan <plan> --task <id> [--worker <worker from args or plan>]`
   Wait for the completion notification, then read the TEAM RESULT summary.
3. Check the work yourself. Don't trust the report:
   - `git diff`: is it in scope, does it match the Design notes and acceptance criteria, any shortcuts or invented APIs?
   - verify line: PASS?
4. Decide:
   - **Good:** `git add -A && git commit -m "<feature>: <id> <title>"`, set `status: done` in the plan.
   - **Small issue (<10 lines):** fix it yourself, re-run `.team/team.sh verify`, commit.
   - **Bigger issue:** `git stash push -u -m "team: rejected <id>"`, then retry once with a different worker and a precise `--note "Previous attempt: <what was wrong>. Do: <what to do instead>"`.
   - **Second failure, or exit code 3 (all workers failed or hit quota):** do the task yourself, using the stashed attempt if it helps.
5. Tell the user in 2–3 lines: who did it, verify result, commit hash, any concerns. Note quota problems in `HANDOFF.md`.

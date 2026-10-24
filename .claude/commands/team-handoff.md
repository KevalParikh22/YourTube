---
description: Update HANDOFF.md so any LLM (or a fresh Claude session) can continue the work
disable-model-invocation: true
allowed-tools: Read, Edit, Bash(git status:*), Bash(git log:*), Bash(git branch:*), Bash(git stash list)
---

Update `HANDOFF.md` from the facts: `git log --oneline -15`, `git status`, the active plan's task statuses, and what happened in this session. Fill in Done, In progress, Next up (with exact `/team-build` or `team.sh` commands), Problems, and worker quota notes. Keep it under 40 lines, set "Last updated" to today with "claude", and don't invent progress.

Then tell the user: if Claude's limit is reached, they can open `gemini` in this repo and say *"Read HANDOFF.md and act as the orchestrator described in .team/ORCHESTRATOR.md, using --chain codex for builds."*

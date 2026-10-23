---
description: Ask a worker LLM (gemini first, big context) a read-only question about the codebase, saving Claude tokens
argument-hint: <question> [--save path.md]
disable-model-invocation: true
allowed-tools: Read, Bash(.team/team.sh ask:*), Bash(git status:*)
---

Run, with Bash `run_in_background: true`:
`.team/team.sh ask $ARGUMENTS`

When it finishes, read the report file from the summary. Spot-check one or two of its `path:line` citations. Give the user a concise answer and point out anything that looked wrong. If the summary warns that files changed, show `git status` and tell the user.

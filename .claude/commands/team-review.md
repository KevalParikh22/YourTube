---
description: Get a worker LLM (codex → gemini) to review the branch diff; Claude triages the findings
argument-hint: [plan-file] [--base <ref>] [extra focus]
disable-model-invocation: true
allowed-tools: Read, Bash(.team/team.sh review:*), Bash(git diff:*), Bash(git log:*)
---

You are the orchestrator. Run, with Bash `run_in_background: true`:
`.team/team.sh review $ARGUMENTS` (put a plan file after `--plan`; text that isn't a flag becomes the extra focus).

Then read the report file named in the summary and triage each finding: open the cited lines, then mark it **real**, **false positive** or **unsure**. Don't pass on unchecked claims.

Show the user only the real and unsure findings (severity, `file:line`, one-line problem), plus the reviewer's verdict and your own. Ask whether to fix them: you fix small ones directly, and bigger ones go back to a worker via `/team-build` or `team.sh build --prompt-file`.

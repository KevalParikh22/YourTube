---
description: Show which worker LLMs (Gemini, Codex, and any optional ones) are installed, the fallback chains, and the repo state
disable-model-invocation: true
allowed-tools: Bash(.team/team.sh status), Bash(git stash list)
---

Run `.team/team.sh status` and show the output to the user in a short table. If a worker is missing, give the one install or login command for it:
- Gemini: `npm i -g @google/gemini-cli`, then run `gemini` once to sign in with the Google account that has Gemini Pro
- Codex: `npm i -g @openai/codex`, then `codex login`
- Gemini: put `GEMINI_API_KEY=...` in `~/.gemini/.env` (see .team/ORCHESTRATOR.md, Worker setup)
- opencode (optional): install from opencode.ai, run `opencode auth login`, set `OPENCODE_MODEL` and add `opencode` to the chains in .team/team.conf
- Kimi (optional, paid plan only): install the Kimi Code CLI from kimi.com/code, run `kimi` once to log in, and add `kimi` to the chains in .team/team.conf

Also mention any `team: failed attempt` stashes, and remind the user that they can drop them with `git stash drop` once they're sure they aren't needed.

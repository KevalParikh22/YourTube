---
description: Show which worker LLMs (Gemini, Kimi, Codex) are installed, the fallback chains, and the repo state
disable-model-invocation: true
allowed-tools: Bash(.team/team.sh status), Bash(git stash list)
---

Run `.team/team.sh status` and show the output to the user in a short table. If a worker is missing, give the one install or login command for it:
- Gemini: `npm i -g @google/gemini-cli`, then run `gemini` once to sign in with the Google account that has Gemini Pro
- Codex: `npm i -g @openai/codex`, then `codex login`
- Kimi: install the Kimi Code CLI from kimi.com/code, then run `kimi` once to log in

Also mention any `team: failed attempt` stashes, and remind the user that they can drop them with `git stash drop` once they're sure they aren't needed.

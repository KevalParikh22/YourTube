# Orchestrator playbook

Claude normally plays the orchestrator. **If Claude is out of usage**, open Gemini CLI in this repo and say:
"Act as the orchestrator described in `.team/ORCHESTRATOR.md`."
Gemini then follows the same steps and leaves itself out of the build chain (`--chain codex`).

---

## Who does what

| Role | Default | Why |
|---|---|---|
| Plan / architecture / hard bugs / final review / commit | **Claude** | Best judgement. Its quota is the scarcest, so spend it on decisions, not typing |
| Main builder | **Gemini** | Biggest quota on Pro, 1M-token context |
| Second builder | **Codex** | Takes over when Gemini is rate-limited |
| Reviewer / small fixes | **Codex** | Small free quota, strong at reviewing code. Keep its prompts short |
| Read-only research ("explain module X", "summarise the repo") | **Gemini** via `team.sh ask` | Huge context, cheap for us |

## Worker setup

Keys live in each tool's own config in your home folder, never in this repo.

- **Gemini:** create `~/.gemini/.env` with one line, `GEMINI_API_KEY=<your key>` (key from aistudio.google.com/apikey). The CLI reads that file in headless runs, which `team.sh` uses. Run `gemini` once by hand to check it works. A free key gives smaller limits than a paid plan, so expect rate limits on long builds; the chain then moves on.
- **Codex:** `codex login`.

**Choosing workers.** The default team is three models: Claude orchestrates, Gemini and Codex work. The chains in `.team/team.conf` decide which workers run and in what order; add or remove names there. Optional workers with ready adapters:
- **opencode:** any OpenRouter, Groq or Ollama model. Run `opencode auth login`, set `OPENCODE_MODEL`, add `opencode` to a chain. Free models are weaker and rate-limited, so they trade quality for fallback.
- **Kimi:** needs a paid Kimi Code plan; run `kimi` once to log in, then add `kimi` to a chain.

## The loop

```
/team-plan  →  for each task:  /team-build  →  verify  →  check diff  →  commit
            →  /team-review (whole branch)  →  fix  →  done
```

### 1. Plan
- Copy `docs/plan/_TEMPLATE.md` to `docs/plan/<feature-slug>.md`.
- Split the work into tasks of **~1–3 files and under ~200 lines each**. Small tasks are what make weaker models succeed.
- Every task gets: files, exact steps, acceptance criteria, a verify command, and a suggested worker.
- Mark tasks that need real judgement (security, data models, concurrency, tricky algorithms) as `worker: claude`.
- If `docs/architecture.md` is empty, fill it first: `team.sh ask "Map this codebase..." --save docs/architecture.md`.

### 2. Build each task
```bash
.team/team.sh build --plan docs/plan/<feature>.md --task T3
```
- This needs a clean git tree on a non-main branch. Commit or stash first.
- The script tries the chain. If a worker fails or hits a rate limit, its partial work is stashed (`git stash list` shows `team: failed attempt ...`) and the next worker starts from a clean tree.
- When it finishes, read the report path printed in the output, then `git diff`.

### 3. Accept or retry
- **Verify passes, diff matches the plan and stays in scope:** `git add -A && git commit -m "<feature>: T3 <title>"`, then tick the task in the plan.
- **Small problem:** fix it yourself if the fix is under ~10 lines, otherwise retry with feedback:
  `team.sh build --plan ... --task T3 --worker codex --note "Previous attempt failed: <error>. Fix by ..."`
- **Second failure on the same task:** stop delegating it and do it yourself (the orchestrator).
- **Exit code 3 (all workers failed or rate-limited):** do it yourself, or pause and write `HANDOFF.md`.

### 4. Review the branch
```bash
.team/team.sh review --base main --plan docs/plan/<feature>.md
```
- Triage the findings yourself. Reviewers produce false positives, so only fix real issues.
- For a high-stakes change (auth, payments, data loss), also review it yourself.

### 5. Handoff
Update `HANDOFF.md` before stopping, and whenever any tool is close to its limit.

## Exit codes of team.sh
| Code | Meaning |
|---|---|
| 0 | A worker finished (verify may still have failed; read the summary) |
| 2 | Bad usage, or the repo is not in a safe state (dirty tree, on main) |
| 3 | Every worker in the chain failed, is missing, or hit its quota. The orchestrator takes over |

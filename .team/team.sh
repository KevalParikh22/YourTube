#!/usr/bin/env bash
# team.sh: run Gemini / opencode / Codex (and optionally Kimi) as workers with automatic fallback.
# Claude (the orchestrator) calls this from its Bash tool. You can also run it by hand.
#
#   team.sh status
#   team.sh build  --plan docs/plan/x.md --task T3 [--worker opencode] [--chain a,b] [--note "..."] [--no-verify] [--allow-dirty]
#   team.sh build  --prompt-file my-task.md        (free-form task instead of a plan task)
#   team.sh ask    "question" [--save docs/file.md] [--worker gemini]
#   team.sh review [--base main] [--plan docs/plan/x.md] [--worker codex]
#   team.sh verify
#
# Exit codes: 0 ok (read the summary) · 2 usage/unsafe repo state · 3 every worker failed → orchestrator takes over
set -uo pipefail

TEAM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$TEAM_DIR/.." && pwd)"
cd "$ROOT" || exit 2
# non-interactive shells (Claude's Bash tool) skip ~/.bashrc, so add the usual per-user CLI folders
for d in "$HOME/.local/bin" "$HOME/.kimi-code/bin" "$HOME/.opencode/bin" "$HOME/.npm-global/bin"; do
  [ -d "$d" ] && case ":$PATH:" in *":$d:"*) ;; *) PATH="$PATH:$d" ;; esac
done
export PATH
# shellcheck source=team.conf
source "$TEAM_DIR/team.conf"
RUNS="$TEAM_DIR/runs"
mkdir -p "$RUNS" && [ -f "$RUNS/.gitignore" ] || echo '*' > "$RUNS/.gitignore"

die()  { echo "team: $*" >&2; exit 2; }
note() { echo "team: $*" >&2; }
installed() { command -v "$1" >/dev/null 2>&1; }

# ---------------- args ----------------
CMD="${1:-help}"; [ $# -gt 0 ] && shift
PLAN="" TASK="" WORKER="" CHAIN_OVERRIDE="" NOTE="" PROMPT_FILE_IN="" BASE="" SAVE="" NO_VERIFY=0 ALLOW_DIRTY=0 TEXT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --plan) PLAN="$2"; shift 2 ;;
    --task) TASK="$2"; shift 2 ;;
    --worker) WORKER="$2"; shift 2 ;;
    --chain) CHAIN_OVERRIDE="${2//,/ }"; shift 2 ;;
    --note) NOTE="$2"; shift 2 ;;
    --prompt-file) PROMPT_FILE_IN="$2"; shift 2 ;;
    --base) BASE="$2"; shift 2 ;;
    --save) SAVE="$2"; shift 2 ;;
    --no-verify) NO_VERIFY=1; shift ;;
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    -h|--help) CMD=help; shift ;;
    --*) die "unknown option $1" ;;
    *) TEXT="${TEXT:+$TEXT }$1"; shift ;;
  esac
done

# chain = override, or default with --worker moved to the front
make_chain() {
  local default="$1" c
  if [ -n "$CHAIN_OVERRIDE" ]; then echo "$CHAIN_OVERRIDE"; return; fi
  if [ -n "$WORKER" ]; then
    c="$WORKER"; for w in $default; do [ "$w" = "$WORKER" ] || c="$c $w"; done; echo "$c"
  else echo "$default"; fi
}

# ---------------- git helpers ----------------
in_git()      { git rev-parse --is-inside-work-tree >/dev/null 2>&1; }
branch()      { git symbolic-ref --short -q HEAD || echo "(detached)"; }
tree_dirty()  { [ -n "$(git status --porcelain)" ]; }
tree_state()  { { git status --porcelain; git diff; } | git hash-object --stdin; }
default_base() {
  local b; for b in main master; do
    git rev-parse -q --verify "$b" >/dev/null && { git merge-base HEAD "$b"; return; }
  done; echo HEAD
}

# ---------------- verify ----------------
detect_verify() {
  [ -n "$VERIFY_CMD" ] && { echo "$VERIFY_CMD"; return; }
  local line
  line="$(grep -m1 -E '^\- \*\*Verify' AGENTS.md 2>/dev/null | sed -nE 's/.*`([^`]+)`.*/\1/p')"
  if [ -n "$line" ] && [[ "$line" != \<* ]]; then echo "$line"; return; fi
  local cmds=()
  if [ -f package.json ]; then
    local pm=npm; [ -f pnpm-lock.yaml ] && pm=pnpm; [ -f yarn.lock ] && pm=yarn; [ -f bun.lockb ] && pm=bun
    for s in lint typecheck test; do
      grep -qE "\"$s\"[[:space:]]*:" package.json && cmds+=("$pm run $s")
    done
  fi
  if [ -f pyproject.toml ] || [ -f requirements.txt ] || [ -f setup.py ]; then
    # prefer the project's own virtualenv over global tools
    local venv="" py=python3 v
    for v in .venv venv; do [ -x "$v/bin/python" ] && { venv="$v/bin/"; py="${venv}python"; break; }; done
    if [ -x "${venv}ruff" ]; then cmds+=("${venv}ruff check ."); elif installed ruff; then cmds+=("ruff check ."); fi
    if [ -x "${venv}pytest" ]; then cmds+=("${venv}pytest -q")
    elif [ -z "$venv" ] && installed pytest; then cmds+=("pytest -q")
    elif [ -d tests ] || [ -d test ]; then cmds+=("$py -m unittest"); fi
  fi
  [ -f Cargo.toml ] && cmds+=("cargo test")
  [ -f go.mod ] && cmds+=("go test ./...")
  [ ${#cmds[@]} -eq 0 ] && [ -f Makefile ] && grep -qE '^test:' Makefile && cmds+=("make test")
  local out="" c
  for c in "${cmds[@]}"; do out="${out:+$out && }$c"; done
  echo "$out"
}

run_verify() {   # $1 = log file → prints PASS/FAIL/SKIPPED
  local cmd; cmd="$(detect_verify)"
  if [ -z "$cmd" ]; then echo "SKIPPED (no verify command, so set VERIFY_CMD in .team/team.conf or the Verify line in AGENTS.md)"; return; fi
  echo "\$ $cmd" > "$1"
  if timeout -k 10 "$TIMEOUT_SECS" bash -c "$cmd" >>"$1" 2>&1; then echo "PASS ($cmd)"; else echo "FAIL ($cmd), see $1"; fi
}

# ---------------- worker runner ----------------
# run_chain MODE(write|read) CHAIN PROMPT_FILE RUN_DIR STASH_ON_FAIL(0|1)
# Sets WINNER, REPORT, ATTEMPTS. Returns 0 if a worker succeeded.
WINNER="" REPORT="" ATTEMPTS=""
run_chain() {
  local mode=$1 chain=$2 pf=$3 dir=$4 stash=$5 w rc log out reason before
  for w in $chain; do
    if ! declare -F "run_$w" >/dev/null; then ATTEMPTS+="$w(no adapter) "; continue; fi
    if ! installed "$w"; then ATTEMPTS+="$w(not installed) "; continue; fi
    log="$dir/$w.log"; out="$dir/$w.out.md"; : > "$out"
    note "trying $w ($mode)… log: ${log#$ROOT/}"
    before="$(tree_state)"
    timeout -k 15 "$TIMEOUT_SECS" bash -c 'source "$1"; "run_$2" "$3" "$4" "$5"' _ \
      "$TEAM_DIR/team.conf" "$w" "$mode" "$pf" "$out" </dev/null >"$log" 2>&1
    rc=$?
    [ -s "$out" ] || cp "$log" "$out"

    reason=""
    if [ $rc -eq 124 ] || [ $rc -eq 137 ]; then reason="timeout"
    elif [ $rc -ne 0 ]; then
      if grep -qiE "$QUOTA_PATTERN" "$log"; then reason="quota"; else reason="exit $rc"; fi
    elif ! [ -s "$out" ] || [ "$(wc -c < "$out")" -lt 20 ]; then reason="empty output"
    # some CLIs exit 0 while only printing a rate-limit message
    elif ! grep -qE 'RESULT:|VERDICT:' "$out" && [ "$(wc -c < "$out")" -lt 2000 ] \
         && grep -qiE "$QUOTA_PATTERN" "$out"; then reason="quota"
    fi

    if [ -z "$reason" ]; then
      if [ "$mode" = read ] && [ "$before" != "$(tree_state)" ]; then
        note "WARNING: $w changed files during a read-only run. Check git status."
        ATTEMPTS+="$w(ok, but modified files!) "
      else ATTEMPTS+="$w(ok) "; fi
      WINNER=$w; REPORT=$out; return 0
    fi

    ATTEMPTS+="$w($reason) "
    note "$w failed: $reason"
    if [ "$mode" = read ] && [ "$before" != "$(tree_state)" ]; then
      note "WARNING: $w changed files during a read-only run before failing. Check git status."
      ATTEMPTS+="[modified files!] "
    fi
    if [ "$stash" = 1 ] && tree_dirty; then
      git stash push -u -q -m "team: failed attempt by $w ${TASK:-task} $(date +%H%M%S)" \
        && ATTEMPTS+="[stashed] "
    fi
  done
  return 1
}

summary() {   # prints the block Claude reads
  echo
  echo "=================== TEAM RESULT ==================="
  echo "mode:      $CMD${TASK:+   task: $TASK}${PLAN:+   plan: $PLAN}"
  echo "attempts:  $ATTEMPTS"
  if [ -n "$WINNER" ]; then
    echo "worker:    $WINNER"
    echo "report:    ${REPORT#$ROOT/}"
    local r; r="$(grep -m1 -E '^#* *(RESULT|VERDICT):' "$REPORT" 2>/dev/null)"
    echo "status:    ${r:-"(worker did not print a RESULT/VERDICT line, so read the report)"}"
  else
    echo "worker:    NONE. Every worker failed (exit 3). Orchestrator: do this yourself or write HANDOFF.md."
  fi
}

new_run_dir() { local d; d="$RUNS/$(date +%Y%m%d-%H%M%S)-$CMD${TASK:+-$TASK}"; mkdir -p "$d"; echo "$d"; }

# ---------------- commands ----------------
cmd_status() {
  echo "repo:     $ROOT"
  in_git && echo "branch:   $(branch)   dirty: $(tree_dirty && echo yes || echo no)" || echo "branch:   (not a git repo)"
  echo "chains:   build=[$BUILD_CHAIN]  review=[$REVIEW_CHAIN]  ask=[$ASK_CHAIN]"
  local vc; vc="$(detect_verify || true)"
  echo "verify:   ${vc:-(none found, so set the Verify line in AGENTS.md or VERIFY_CMD in .team/team.conf)}"
  for w in gemini opencode kimi codex claude; do
    if installed "$w"; then printf "  %-7s ✓ %s\n" "$w" "$(timeout 10 "$w" --version 2>/dev/null | head -1)"
    else printf "  %-7s ✗ not installed\n" "$w"; fi
  done
  local s; s="$(git stash list 2>/dev/null | grep -c 'team: failed attempt' || true)"
  [ "${s:-0}" -gt 0 ] && echo "stashes:  $s failed-attempt stash(es) (git stash list)"
  return 0
}

extract_task() {  # prints the "### T3…" section of the plan
  awk -v id="$TASK" '
    $0 ~ "^###[[:space:]]+" id "([^0-9A-Za-z]|$)" {p=1; print; next}
    p && /^##/ {exit}
    p {print}' "$PLAN"
}

cmd_build() {
  in_git || die "build needs a git repo (so failed attempts can be stashed and diffs reviewed)"
  local b; b="$(branch)"
  for p in $PROTECTED_BRANCHES; do [ "$b" = "$p" ] && die "on '$b'. Create a branch first: git switch -c feat/<name>"; done
  if tree_dirty && [ $ALLOW_DIRTY -eq 0 ]; then
    die "working tree is dirty. Commit or stash first (or pass --allow-dirty, which turns off stash-on-failure)"
  fi
  local stash=1; [ $ALLOW_DIRTY -eq 1 ] && stash=0

  local dir pf; dir="$(new_run_dir)"; pf="$dir/prompt.md"
  cat "$TEAM_DIR/prompts/build.md" > "$pf"
  if [ -n "$PROMPT_FILE_IN" ]; then
    [ -f "$PROMPT_FILE_IN" ] || die "no such prompt file: $PROMPT_FILE_IN"
    printf '\n---\n# TASK\n' >> "$pf"; cat "$PROMPT_FILE_IN" >> "$pf"
  else
    [ -n "$PLAN" ] && [ -n "$TASK" ] || die "build needs --plan and --task (or --prompt-file)"
    [ -f "$PLAN" ] || die "no such plan: $PLAN"
    local section; section="$(extract_task)"
    [ -n "$section" ] || die "task '$TASK' not found in $PLAN (expected a heading like '### $TASK: title')"
    printf '\n---\nPlan file: %s (read its Design notes)\nYour task id: %s\n\n%s\n' "$PLAN" "$TASK" "$section" >> "$pf"
  fi
  [ -n "$NOTE" ] && printf '\n## Orchestrator note (important)\n%s\n' "$NOTE" >> "$pf"

  if ! run_chain write "$(make_chain "$BUILD_CHAIN")" "$pf" "$dir" "$stash"; then summary; exit 3; fi
  git diff --stat > "$dir/diffstat.txt"; git status --porcelain >> "$dir/diffstat.txt"
  summary
  echo "changed:"; git status --short | sed 's/^/  /'
  if [ $NO_VERIFY -eq 0 ]; then echo "verify:    $(run_verify "$dir/verify.log")"; else echo "verify:    skipped (--no-verify)"; fi
  echo "next:      review 'git diff', then commit, or retry with --worker <other> --note \"what was wrong\""
}

cmd_ask() {
  [ -n "$TEXT" ] || [ -n "$PROMPT_FILE_IN" ] || die 'ask needs a question: team.sh ask "..."'
  local dir pf; dir="$(new_run_dir)"; pf="$dir/prompt.md"
  cat "$TEAM_DIR/prompts/ask.md" > "$pf"
  [ -n "$TEXT" ] && echo "$TEXT" >> "$pf"
  [ -n "$PROMPT_FILE_IN" ] && cat "$PROMPT_FILE_IN" >> "$pf"
  if ! run_chain read "$(make_chain "$ASK_CHAIN")" "$pf" "$dir" 0; then summary; exit 3; fi
  if [ -n "$SAVE" ]; then mkdir -p "$(dirname "$SAVE")"; cp "$REPORT" "$SAVE"; fi
  summary
  [ -n "$SAVE" ] && echo "saved:     $SAVE"
  return 0
}

cmd_review() {
  in_git || die "review needs a git repo"
  [ -n "$BASE" ] || BASE="$(default_base)"
  local dir pf diff; dir="$(new_run_dir)"; pf="$dir/prompt.md"; diff="$dir/diff.patch"
  git diff "$BASE" > "$diff"
  # include new untracked files too
  git ls-files --others --exclude-standard -z | xargs -0 -r -I{} git diff --no-index /dev/null {} >> "$diff" 2>/dev/null
  [ -s "$diff" ] || die "nothing to review (no changes vs $BASE)"
  cat "$TEAM_DIR/prompts/review.md" > "$pf"
  [ -n "$PLAN" ] && printf '\nPlan file: %s\n' "$PLAN" >> "$pf"
  [ -n "$TEXT" ] && printf '\nExtra focus from the orchestrator: %s\n' "$TEXT" >> "$pf"
  printf '\n# DIFF (vs %s)\n```diff\n' "$BASE" >> "$pf"
  if [ "$(wc -c < "$diff")" -gt "$MAX_DIFF_BYTES" ]; then
    head -c "$MAX_DIFF_BYTES" "$diff" >> "$pf"
    printf '\n... [diff truncated at %s bytes. Full diff at %s; open the files directly for the rest]\n' "$MAX_DIFF_BYTES" "${diff#$ROOT/}" >> "$pf"
  else cat "$diff" >> "$pf"; fi
  printf '```\n' >> "$pf"
  if ! run_chain read "$(make_chain "$REVIEW_CHAIN")" "$pf" "$dir" 0; then summary; exit 3; fi
  summary
  echo "next:      triage findings yourself. Reviewers produce false positives."
}

cmd_verify() { local d; d="$(new_run_dir)"; echo "verify: $(run_verify "$d/verify.log")"; [ -f "$d/verify.log" ] && tail -40 "$d/verify.log"; return 0; }

case "$CMD" in
  status) cmd_status ;;
  build)  cmd_build ;;
  ask)    cmd_ask ;;
  review) cmd_review ;;
  verify) cmd_verify ;;
  help|*) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//' ;;
esac

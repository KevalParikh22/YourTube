# Plan: Reliability: clear failures, a log file, always-on dashboard, clean public repo

**Goal:**
- A failed playlist sync explains what went wrong and how to fix it. **Try again** and **Dismiss** work, and a re-sync uses the same access (sign-in, cookies.txt or public) that the playlist was added with.
- Every failure is written to a descriptive log file, `logs/yourtube.log`, with the cause, the fix and the details needed to debug it. Secrets are never written.
- One command installs the dashboard as a background service. It starts at login and restarts if it crashes.
- AI and team files (CLAUDE.md, AGENTS.md, GEMINI.md, HANDOFF.md, `.claude/`, `.team/`, `docs/plan/`) are git-ignored and removed from the repo. They stay on disk, so the team workflow keeps working.

**Branch:** feat/reliability
**Out of scope:**
- Rewriting git history. The old commits keep the AI files; the user chose "untrack from now on".
- Making the repo public (the user's call).
- Remote or central logging.
- A Windows service beyond an untested Task Scheduler script.

## Decisions (2026-10-29, from the user)
- Failed sync: the error **sticks** after a refresh, and **retrying also fails**. Fix both.
- Always on: **start at login** as a user service (Linux systemd, macOS LaunchAgent; Windows Task Scheduler, untested), with uninstall.
- Git: **untrack from now on** via `.gitignore` + `git rm --cached`; no history rewrite.

## Findings that shape the design
- `Job` keeps the last task in memory until the server restarts. The Playlists page shows its error (`lastError(job.log)`) with no way to clear it, so a refresh shows it again.
- The access mode (public / browser / cookies.txt) is **not stored per playlist**. The row's **Sync** button (`data-act="resync"`) reuses whatever mode is selected in the add form (`S.ingest.access`, remembered per browser). A private playlist added with a browser sign-in can therefore be re-synced as "public" and fail with "playlist does not exist". This is the likely reason retries fail; the new log will confirm other causes.
- Nothing is logged today: `Handler.log_message` is silenced, job output lives only in memory, and an exception in `do_GET`/`do_POST` prints a traceback to stderr and drops the connection.
- `main()` prints the token link to stdout. Under a service, stdout goes to the journal, so the link must not be printed there.

## Design notes

### A. `applog.py` (new): the log file (T1)
- `LOG_DIR = os.environ.get("YTKB_LOG_DIR") or os.path.join(ROOT_DIR, "logs")`, `LOG_FILE = os.path.join(LOG_DIR, "yourtube.log")`. `tests/__init__.py` sets `YTKB_LOG_DIR` to the temp dir.
- `setup(level=logging.INFO) -> logging.Logger`: idempotent. It creates `LOG_DIR` and attaches to the logger `"yourtube"`:
  - a `RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=5, encoding="utf-8")`;
  - the format `"%(asctime)s %(levelname)-7s %(name)s: %(message)s"` (datefmt `%Y-%m-%d %H:%M:%S`);
  - a `RedactFilter`;
  - `propagate = False`.

  If the folder can't be created (read-only drive), it falls back to a `NullHandler` and prints one `Warning:` line. The app must never crash because of logging.
- `get(name) -> logging.Logger`: returns `logging.getLogger("yourtube." + name)`.
- `redact(text: str) -> str` replaces each of these with `[hidden]`:
  - `t=<value>` in URLs (the dashboard token), and the configured token itself when `dashboard.TOKEN` is set (passed in via `set_secret(value)`);
  - `api_key`/`key`/`token` query or JSON values;
  - `Authorization:` / `Cookie:` / `x-api-key:` header values;
  - strings matching `sk-ant-[\w-]{10,}`, `sk-[\w-]{20,}` and `AIza[\w-]{20,}`;
  - any value registered with `set_secret()` (the saved API keys, registered at startup from `llm.load_settings()`).

  `RedactFilter.filter(record)` sets `record.msg = redact(record.getMessage())`, `record.args = None`, and redacts `record.exc_text` once formatted (override `format` in a small `Formatter` subclass to redact the traceback text too).
- `log_task(kind, label, ok, stopped, started_at, finished_at, output)` writes, depending on the outcome:
  - success: `INFO  yourtube.job: Task done: <label> (kind=sync, 12.4 s)`;
  - stopped: a `WARNING ... Task stopped: ...` line;
  - failure: an `ERROR` record:
    ```
    Task failed: Sync https://www.youtube.com/playlist?list=WL (kind=sync, 3.1 s)
      Cause: <from explain(output), or the last "Error:" line>
      Fix: <from explain(output), or "See the output below">
      Last output:
        <last 40 non-empty lines of output, each indented 4 spaces>
    ```
  Every `Warning:` line in a successful task's output is logged as one `WARNING` record, with the task label.
- **Dashboard integration** (`dashboard.py`):
  - `main()` calls `applog.setup()`, `applog.set_secret(TOKEN)` and each saved API key. It then logs `INFO Dashboard started: port=8765, python=3.x, yt-dlp=<version>, platform=<sys.platform>, db=<path>`. The token never appears.
  - `Job._run` calls `applog.log_task(...)` after every task. In its `except Exception` branch it also calls `log.exception("Unexpected error in task %s", label)`, so the full traceback goes to the file while the job log keeps its short line.
  - Rename `do_GET`/`do_POST` to `_do_get`/`_do_post`, and add `do_GET`/`do_POST` wrappers. On an exception, each wrapper calls `log.exception("Request failed: %s %s", method, url.path)` (path only, never the query) and returns 500 `{"error": "Something went wrong. Details are in logs/yourtube.log"}`. If the headers were already sent, it only logs.
  - 4xx replies stay unlogged (they are user input errors shown in the UI), except 401/403, which log one `WARNING` line with path and reason (useful for "the page won't open").

### B. `explain(output) -> Optional[Tuple[str, str]]`: causes and fixes (T2)
- Lives in `applog.py`. It holds an ordered list `KNOWN = [(regex, cause, fix), ...]`, and the first match on the task output wins. At least:

| Pattern (case-insensitive) | Cause | Fix |
|---|---|---|
| `playlist does not exist` / `This playlist type is unviewable` | The playlist ID is wrong, or the playlist is private and was read without your sign-in | Check the link. For private playlists and Watch Later choose "Use my browser login" or a cookies.txt file |
| `Sign in to confirm you.?re not a bot` | YouTube asked for a sign-in check | Sync with your browser login, wait a few minutes, or update yt-dlp: `.venv/bin/python -m pip install -U yt-dlp` |
| `HTTP Error 429` / `Too Many Requests` | YouTube is rate-limiting this computer | Wait 15–30 minutes, then try again |
| `could not find .* cookies database` / `failed to decrypt` / `secretstorage` | The browser's cookies could not be read | Sign in to YouTube in that browser, close it, and try again; on Linux install `secretstorage`; or use a cookies.txt export |
| `Unsupported URL` / `is not a valid URL` | The link is not a YouTube playlist or video | Paste a link like https://www.youtube.com/playlist?list=… |
| `getaddrinfo` / `Name or service not known` / `timed out` / `Connection (refused\|reset)` / `URLError` | No connection to YouTube | Check the internet connection and try again |
| `nsig extraction failed` / `Unable to extract` / `Requested format is not available` | YouTube changed something that yt-dlp does not handle yet | Update yt-dlp: `.venv/bin/python -m pip install -U yt-dlp` |
| `database is locked` | Another program is using the library database | Close other YourTube windows or scripts and try again |
| `No space left on device` | The disk is full | Free some space |
| `Connection refused.*11434` / `Ollama` + `not running` | Ollama is not running | Start Ollama (`ollama serve`) |

- `main.run_pipeline`: in its `except` branch, after the existing tips, add `cause, fix = applog.explain(str(e)) or (None, None)`. If it matches, print `Cause: …` and `Fix: …` (plain text, each on its own line). It also calls `applog.get("sync").error(...)` with the exception text and the access mode used (`public` / `browser:<name>` / `cookies`). It never logs the cookie file contents.
- Client `lastError(log)` also picks up a `Fix:` line (see C).

### C. Failed sync: stored access, Try again, Dismiss (T3 server, T4 client)
- **Storage:** `init_db()` migration adds `playlists.access TEXT` (`'public' | 'browser:<name>' | 'cookies'`) and `playlists.cookies_file TEXT`. `save_playlist(playlist_info, …)` stores `playlist_info.get("access")` / `cookies_file` when they are present (`COALESCE`, so an older caller doesn't wipe them). `run_pipeline` puts them into `playlist_info`: `browser:<detected browser>` when `auto` resolved to a browser.
- **`POST /api/ingest`** also accepts `{playlist_id}` (resync). With no explicit `access` in the body, it reads the stored `access`/`cookies_file` for that playlist and uses them (`browser:auto` stays auto; a stored cookies file that no longer exists → 400 "cookies.txt file not found: choose access again"). An unknown id → 404. The existing `{url, access, …}` form is unchanged.
- **`Job.retry(task_id) -> Optional[dict]`:** `Job` keeps `{id: (kind, label, fn, key)}` for the tasks in `self.done` (pruned with it). `retry` re-queues the same fn when that id is the most recent finished task and it failed. It returns `start(...)`'s result, or None. `POST /api/job/retry {id}` replies through `_queued`, with 404 when the task can't be retried.
- **`Job.dismiss(task_id)`:** sets `state["dismissed"] = True` when `state["id"] == task_id` and the task is not running. `snapshot()` includes `dismissed`. `POST /api/job/dismiss {id}` → `{ok}`. A new task resets the flag (it starts with a new state).
- **Client** (T4):
  - The Playlists page failure line becomes a card (`.banner warn`) showing: "Sync failed", the cause (`Error:` line), the fix (`Fix:` line when present) and the buttons **Try again** (`data-act="job-retry"`), **Dismiss** (`data-act="job-dismiss"`) and **See details** (expands the last 40 lines of the job log in a `<pre>`, escaped).
  - The card hides when `job.dismissed`, or once a newer sync succeeds.
  - The row's **Sync** button sends `{playlist_id}` instead of the form's access.
  - On the Playlists table, a row whose stored access is `browser:*` or `cookies` shows a small faint label ("with your Chrome login" / "with cookies.txt").
- **Help page** (T4): a **Logs** section with the log path, a "Show recent problems" button and a **Download log** link.
  - The button calls `GET /api/logs?level=warning&lines=200` and shows the matching redacted lines in a `<pre>`, escaped.
  - **Download log** goes to `/download/yourtube.log`, added to `DOWNLOADS` in T3 with `text/plain; charset=utf-8`; it serves the current file only. `GET /api/logs` returns `{path, lines: [...]}`, at most 1,000 lines, filtered to records at or above `level`, with continuation lines kept with their record.

### D. Start at login (T5)
- **`scripts/service.sh install|uninstall|status|open`** (bash, Linux and macOS; `set -euo pipefail`). It finds the repo root from the script path and requires `.venv/bin/python`.
  - **Linux:** writes `~/.config/systemd/user/yourtube.service`:
    ```
    [Unit]
    Description=YourTube dashboard
    StartLimitIntervalSec=0
    [Service]
    WorkingDirectory=<repo>
    ExecStartPre=/usr/bin/test -d "<repo>"
    ExecStart="<repo>/.venv/bin/python" "<repo>/dashboard.py" --no-open --port <port>
    Restart=on-failure
    RestartSec=15
    [Install]
    WantedBy=default.target
    ```
    Then `systemctl --user daemon-reload && systemctl --user enable --now yourtube.service`. `Restart=always` with `StartLimitIntervalSec=0` covers a repo on a removable drive that mounts after login: it keeps retrying every 15 s until the folder exists. Paths with spaces must work ("New Volume").
    - `uninstall`: `systemctl --user disable --now` and remove the file.
    - `status`: `systemctl --user status --no-pager`.
  - **macOS:** `~/Library/LaunchAgents/com.yourtube.dashboard.plist` with `RunAtLoad` and `KeepAlive` true, `ProgramArguments` as above, `WorkingDirectory`, and `StandardOutPath`/`StandardErrorPath` = `<repo>/logs/service.log`. Loaded with `launchctl bootstrap gui/$(id -u)` (uninstall: `bootout`).
  - `open` (both OSes): runs `.venv/bin/python dashboard.py --print-url` and opens it (`xdg-open` / `open`).
  - `--port N` option, default 8765.
- **`scripts/service.ps1 -Install|-Uninstall|-Status|-Open`** (Windows, untested): a Task Scheduler task "YourTube dashboard" at logon, running `.venv\Scripts\pythonw.exe dashboard.py --no-open`, restarting on failure 3 times.
- **`dashboard.py` main():**
  - Print the token link only when `sys.stdout.isatty()`. Otherwise print `YourTube dashboard running on http://127.0.0.1:<port>. Get the link with: .venv/bin/python dashboard.py --print-url`.
  - If the port is in use (`OSError` with `errno.EADDRINUSE` on bind), print `YourTube is already running on port <port> (or another program uses it). Open it with: .venv/bin/python dashboard.py --print-url` and exit with code 0 when something answers `GET /api/job` with 401/200 on that port, else 1. Log it too.
- `scripts/setup.sh` ends with one line suggesting `bash scripts/service.sh install` to keep it running.

### E. Clean repo (T6)
- Append to `.gitignore`:
  ```
  # AI assistant and team-workflow files (kept locally, not published)
  CLAUDE.md
  AGENTS.md
  GEMINI.md
  HANDOFF.md
  .claude/
  .team/
  docs/plan/
  # Logs
  logs/
  ```
- `git rm -r --cached` those paths (files stay on disk).
- Keep `docs/architecture.md`, the PRDs, `docs/MANUAL-TESTS.md`, `spikes/`. Remove any links from README/SETUP/SECURITY that point to the untracked files.
- CI must still pass, since tests don't read those files.

## Tasks

### T1: Log file, redaction, task and request logging
- **worker:** codex
- **status:** done
- **files:** `applog.py` (new), `dashboard.py`, `tests/__init__.py`, `tests/test_applog.py` (new)
- **steps:** Design notes A (not `explain`, which is T2).
- **acceptance:** [ ] a failed task writes an ERROR record with label, kind, duration and last output; [ ] an exception in a request returns 500 JSON and logs the traceback; [ ] tests prove the token, an `sk-ant-` key, an `AIza` key, a `Cookie:` header and a registered secret never reach the file; [ ] logging can't crash the app (unwritable dir test); [ ] rotation configured.
- **verify:** `.venv/bin/python -m unittest`

### T2: Cause and fix for known failures
- **worker:** agy
- **status:** done
- **depends on:** T1
- **files:** `applog.py`, `main.py`, `tests/test_applog.py`
- **steps:** Design notes B.
- **acceptance:** [ ] each row of the table has a test; [ ] `run_pipeline` prints `Cause:`/`Fix:` and logs the failure with the access mode; [ ] `log_task` uses `explain`.
- **verify:** `.venv/bin/python -m unittest`

### T3: Stored access per playlist; retry, dismiss and logs endpoints
- **worker:** codex
- **status:** done
- **depends on:** T1
- **files:** `storage.py`, `main.py`, `dashboard.py`, `tests/test_dashboard.py`, `tests/test_storage.py`
- **steps:** Design notes C (server parts: migration, `save_playlist`, `run_pipeline` access, `/api/ingest {playlist_id}`, `Job.retry`/`dismiss`, `/api/job/retry`, `/api/job/dismiss`, `GET /api/logs`, the `yourtube.log` download).
- **acceptance:** [ ] a resync by `playlist_id` uses the stored access; [ ] a retry re-queues only the latest failed task; [ ] dismiss hides it until the next task; [ ] `/api/logs` filters by level and caps lines; [ ] migration works on an old DB; [ ] tests for each.
- **verify:** `.venv/bin/python -m unittest`

### T4: Failure card, Try again, Dismiss, Help page logs
- **worker:** codex
- **status:** done
- **depends on:** T2, T3
- **files:** `web/index.html`
- **steps:** Design notes C (client parts).
- **acceptance:** [ ] after a failed sync, the card shows cause and fix and survives a refresh until dismissed or a later success; [ ] Try again re-runs it; [ ] row Sync uses stored access; [ ] Help shows recent problems and downloads the log; [ ] all log text escaped.
- **verify:** `.venv/bin/python -m unittest` and a manual check

### T5: Start at login
- **worker:** agy
- **status:** done
- **files:** `scripts/service.sh` (new), `scripts/service.ps1` (new), `scripts/setup.sh`, `dashboard.py` (main only), `tests/test_dashboard.py`
- **steps:** Design notes D.
- **acceptance:** [ ] `bash -n scripts/service.sh` passes; [ ] the unit text quotes paths with spaces; [ ] a test covers the port-in-use message and the non-TTY start line without the token.
- **verify:** `.venv/bin/python -m unittest && bash -n scripts/service.sh`

### T6: Clean repo, docs, real install
- **worker:** claude
- **status:** todo
- **depends on:** T1–T5
- **files:** `.gitignore`, `README.md`, `docs/SETUP.md` (sections "Keep it running" and "Logs", troubleshooting rows), `docs/MANUAL-TESTS.md` (session S9), `SECURITY.md` (logs never hold secrets)
- **steps:** Design notes E. Install the service on this machine with the user's OK, check that it restarts after a kill, and confirm a real failed sync shows cause and fix and lands in the log.
- **verify:** `.venv/bin/python -m unittest`; `git ls-files | grep -E "CLAUDE|AGENTS|GEMINI|HANDOFF|^\.claude|^\.team|docs/plan"` prints nothing

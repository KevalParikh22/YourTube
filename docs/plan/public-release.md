# Plan: Public release readiness

**Goal:** The repo can be made public safely: it has a licence and disclaimers, explains how cookies and keys are handled, protects the dashboard on shared computers with an access token, carries no notes about the author's own machine, has install notes per OS, and runs its tests on every push.
**Branch:** feat/public-release
**Out of scope:** making the GitHub repo public (the user flips that when ready), renaming the project (the user keeps "YourTube"), multi-user or hosted versions.

## Decisions (2026-10-28)
- Licence: **MIT**, copyright "Keval Parikh".
- Access token: **always on**.
- Name: **keep YourTube**; the README says it is unofficial and not affiliated with YouTube or Google.
- Already done before this plan: commit author rewritten to the GitHub noreply address; system fonts (no Google Fonts); cost estimates before rating and summary runs, with the default provider "none".

## Design notes

### T1: Licence, README disclaimer, SECURITY.md
- `LICENSE`: the standard MIT text, `Copyright (c) 2026 Keval Parikh`.
- `README.md`: a **Disclaimer** section: unofficial, not affiliated with or endorsed by YouTube or Google; uses yt-dlp, which YouTube changes can break; it reads your own account with your own browser sign-in, at your own risk; it never changes anything on YouTube (read-only). Add a **License** line (MIT, link to LICENSE) and a link to SECURITY.md.
- `SECURITY.md`:
  - **Cookies:** read locally from your browser or cookies.txt, only to list playlists; sent only to youtube.com; never stored or logged. Per-video reads are anonymous.
  - **API keys:** live in `settings.json`, created readable only by you. Environment variables (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`) are the alternative; use them on NTFS/FAT drives, where file permissions aren't enforced. Keys are never sent to the browser.
  - **The dashboard:** listens only on 127.0.0.1, needs the access token (T3), checks the Host header and the `X-YTKB` header on writes.
  - **AI:** only titles, descriptions, transcripts and questions go to the AI you choose; with Local only, nothing leaves the computer.
  - **Reporting a problem:** open a GitHub issue without secrets, or contact the maintainer through GitHub.

### T2: Per-OS install notes; remove notes about the author's machine
- `docs/SETUP.md`: a section **"Notes per operating system"**:
  - **Linux:** Chrome cookies need the `secretstorage` package (in requirements.txt) and an unlocked keyring; Firefox works without.
  - **Windows:** Chrome, Edge and Brave use app-bound encryption, so their cookies usually can't be read; use Firefox or a cookies.txt export. The setup script is `scripts\setup.ps1` (not yet tested on Windows).
  - **macOS:** reading Chrome cookies triggers a Keychain prompt; allow it, or use Firefox.
- Remove sentences about the author's own computer or library from `CLAUDE.md`, `AGENTS.md`, `docs/SETUP.md`, `docs/architecture.md` and `README.md`. For example, CLAUDE.md says "On this Linux machine that is Chrome, because Firefox is not signed in" (keep the general rule, drop the personal fact). **Keep** the measured results in `docs/PRD-*.md` and `docs/plan/*` (they're data, written as hardware specs, for example "RTX 3050 4 GB"), and keep `docs/MANUAL-TESTS.md` as is.

### T3: Dashboard access token (security, claude)
- `settings.json["dashboard_token"]`: created on first start with `secrets.token_urlsafe(24)` and saved (the file is owner-only). Never sent by `public_settings()`.
- `dashboard.TOKEN` (module global) is set by `main()`. When it is `None` (tests that build a server directly), checks are off. A dedicated test turns it on.
- **Getting in:** `GET /?t=<token>` with the right token sets the cookie `ytkb_token=<token>; HttpOnly; SameSite=Strict; Path=/; Max-Age=31536000` and redirects (303) to `/`. After that, plain `http://127.0.0.1:8765/` works in that browser. `main()` opens the browser at the token URL and prints it ("Open: http://127.0.0.1:8765/?t=…").
- **Every other request** (`/`, `/api/*`, `/frames/*`, `/download/*`, `/docs/setup`, POSTs) needs the cookie, compared with `hmac.compare_digest`. Otherwise:
  - `/` returns a small HTML page (200): "Open the link printed in the terminal where you started the dashboard, or run `.venv/bin/python dashboard.py --print-url`".
  - Everything else returns `401 {"error": "Open the dashboard link with its access token"}`.
- `dashboard.py --print-url` prints the token URL and exits (for a dashboard that's already running).
- The existing Host check and the `X-YTKB` + JSON checks on POSTs stay.
- Tests: no cookie → 401 for an API call and the help page for `/`; a wrong `?t=` → help page, no cookie; the right `?t=` → 303 plus `Set-Cookie` with HttpOnly and SameSite=Strict; with the cookie → 200; the token never appears in `/api/state`.
- Docs: SETUP.md "Start the dashboard" and Troubleshooting ("Open the link printed in the terminal…"); SECURITY.md (T1) already mentions the token.

### T4: CI
- `.github/workflows/tests.yml`: on push and pull_request; `ubuntu-latest`; Python 3.12 (`actions/setup-python`); `python -m pip install -r requirements.txt`; `python -m unittest -v`. No secrets; live tests stay skipped (no `YTKB_LIVE`).
- If a test needs a tool the runner lacks (for example ffmpeg), skip it with `unittest.skipUnless(shutil.which("ffmpeg"), ...)` instead of installing it, and list such tests in the report.

## Tasks

### T1: Licence, README disclaimer, SECURITY.md
- **worker:** agy
- **status:** done
- **files:** `LICENSE` (new), `README.md`, `SECURITY.md` (new)
- **acceptance:** [ ] the three files say what Design notes T1 lists; [ ] no claim the code doesn't back up.
- **verify:** `.venv/bin/python -m unittest`

### T2: Per-OS notes; remove notes about the author's machine
- **worker:** agy
- **status:** done (orchestrator removed a duplicated OS section)
- **files:** `docs/SETUP.md`, `CLAUDE.md`, `AGENTS.md`, `docs/architecture.md`, `README.md`
- **acceptance:** [ ] the per-OS section exists; [ ] `grep -rniE "this (linux )?machine|my (laptop|machine)|keval" CLAUDE.md AGENTS.md docs/SETUP.md docs/architecture.md README.md` finds nothing personal (the LICENSE copyright line is expected).
- **verify:** `.venv/bin/python -m unittest`

### T3: Dashboard access token
- **worker:** claude
- **status:** done
- **files:** `dashboard.py`, `llm.py` (keep the token out of public settings), `tests/test_dashboard.py`, `docs/SETUP.md`
- **acceptance:** [ ] Design notes T3 behaviour; [ ] tests listed there; [ ] the real dashboard works after following the printed link.
- **verify:** `.venv/bin/python -m unittest`

### T4: CI workflow
- **worker:** codex
- **status:** done
- **files:** `.github/workflows/tests.yml` (new), test files only if a skip is needed
- **acceptance:** [ ] the workflow is valid YAML that runs the suite; [ ] it passes on GitHub after the push.
- **verify:** `.venv/bin/python -m unittest`

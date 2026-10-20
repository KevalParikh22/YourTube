# Phase 0: Research Review & Improvement Plan
**Project**: YT KB (YouTube Knowledge Base)
**Companion to**: [PRD-Phase-0.md](PRD-Phase-0.md) (v0.1.0)
**Date**: October 20, 2026
**Status**: Proposed PRD v0.2 changes. The items marked *Done* are already implemented.

---

## 1. Summary

The Phase 0 goal is still right: turn an overwhelming private playlist into topic groups with honest time-to-complete numbers, without ever touching the source playlist. Research changes **how** some of it should be built:

1. **The YouTube Data API cannot read Watch Later.** Watch Later is the playlist users most want to triage. The code's switch from OAuth plus the Data API to **yt-dlp with cookies** is therefore required, not just convenient. The PRD should adopt it officially.
2. **On Windows, reading Chrome or Edge cookies is unreliable** because of Chromium's app-bound cookie encryption. **Firefox or an exported `cookies.txt`** has to be the documented default.
3. **Writing playlists back to YouTube** is the only step that needs the API, and it costs 50 units per video. That makes it a paced, opt-in, multi-day job. It is now **Phase 0.4b, after the local dashboard**.
4. A **static HTML report was not enough** to "navigate everything". Progress tracking, re-categorization and planning need a small **local dashboard with write access to the DB**. *Done*: `python dashboard.py`.

---

## 2. PRD vs. implementation: gap matrix

| PRD requirement | Before this review | Now | Notes |
| :--- | :--- | :--- | :--- |
| AC 1.1 OAuth with read-only scope | Replaced by yt-dlp | yt-dlp (kept) | The API can't read Watch Later (§3.1). OAuth is only needed for write-back. |
| AC 1.2 Encrypted local tokens | n/a | n/a | Cookies are the credential now (§3.5). |
| AC 1.3 Pagination to 1,000+ items | yt-dlp handles it | Same | Not yet tested live on 1,000+ items. |
| AC 1.4 Flag deleted/private videos | Silently skipped | **Counted and reported** | Ingest log shows "Skipped N unavailable". |
| AC 2.1 Title, description, tags, channel, duration | Title, channel, duration only | Same | Flat extraction has no description or tags. See §5 S2 for the trade-off. |
| AC 2.2 Normalized taxonomy | Hard-coded keywords | **Editable taxonomy (`taxonomy.json`)** | Edit it on the Categories page. |
| AC 2.3 Custom categories / suggested clusters | None | **Custom: done.** Clusters: proposed (§6 P1) | |
| AC 2.4 Multi-category + primary | Single category | Single category + **manual lock** | Multi-tagging is deferred. It adds UI cost for little Phase 0 value. |
| AC 3.x Create `[YT-KB] …` playlists | Not built | Not built (designed in §5 S6) | Queue links cover playback in the meantime. |
| AC 4.1–4.3 Duration math, 1×–2× | Done | Done | Durations come as seconds from yt-dlp, so no ISO 8601 parsing is needed. |
| AC 4.4 Unwatched / In progress / Completed | DB functions only, no UI | **Done in the dashboard** | Remaining time updates live. |
| §8 `video_categories` table | Not built | Category stays a column, plus a **`playlist_items` table** | Membership was the real gap (§4). |
| §7 Quota safeguards | n/a (no API use) | n/a | Applies only to write-back. |

---

## 3. Research findings

### 3.1 Watch Later is not available through the Data API
Since 2016, the `watchLater` playlist ID is returned as the placeholder `WL`, and `playlistItems.list` returns **empty results** for watch history and Watch Later. Google's older sample docs still show the old behaviour, so the sources conflict, but the change note and community reports agree that WL reads return nothing.
**Impact**: Phase 0 cannot meet its main use case on the Data API. yt-dlp reads the logged-in web view, so it can.
**Action**: *Done*. The extractor and dashboard accept `WL` / "watch later" as input.

### 3.2 Chromium cookie encryption on Windows
Chrome 127+ (and other Chromium browsers) use **app-bound encryption** for cookies. yt-dlp's `--cookies-from-browser chrome` on Windows often fails with a DPAPI error. A separate common failure is "Could not copy Chrome cookie database" when the browser is open and holds a lock on the file. Workarounds keep breaking.
**Action**: *Done*. Firefox is the default browser on the Ingest page. A warning appears when a Chromium browser is chosen, the `cookies.txt` route is first-class, and the CLI tips are updated.

### 3.3 YouTube Data API quota (write-back only)
| Call | Cost (units) |
| :--- | :--- |
| `playlistItems.list`, `videos.list` | 1 |
| `playlists.insert` | 50 |
| `playlistItems.insert` | 50 per video |

- The default daily quota is 10,000 units. It resets at midnight Pacific Time, and **failed calls are still charged**.
- A full write of 300 videos costs about 15,000+ units, which is **2+ days** of quota.
- A practical budget is about 190 videos per day, keeping headroom for playlist creation and retries.
- If the OAuth app is left in Google Cloud's *Testing* publishing status, refresh tokens expire after about 7 days. Users will need to re-consent unless the app is published, or used only for short write sessions.

### 3.4 `watch_videos` queue links
`https://www.youtube.com/watch_videos?video_ids=…` is **undocumented**. It plays up to **50** IDs as a temporary queue. The original code silently dropped everything after the first 50.
**Action**: *Done*. Queues are split into chunks of 50 ("Play 1/3, 2/3…") everywhere. Because the endpoint is undocumented, it is a dependency at risk. If YouTube removes it, fall back to opening the first video, or to write-back.

### 3.5 yt-dlp operational risks
- YouTube changes often break extractors. **Keep yt-dlp updated** (`pip install -U yt-dlp`). Version 2026.08.19 is installed.
- Heavy or anonymous use can trigger "Sign in to confirm you're not a bot". Logged-in cookies and an occasional (not continuous) sync reduce this.
- A `cookies.txt` file grants **full access to the YouTube/Google session**. Never commit or share it. Store it outside the project folder, or git-ignore it once the folder becomes a repository.

### Sources
- [YouTube Data API: quota costs](https://developers.google.com/youtube/v3/determine_quota_cost)
- [Playlist automation quota example](https://rapidevelopers.com/api-automations/how-to-automate-youtube-playlist-management-using-the-api)
- [YouTube API quota overview (reset time, failed calls)](https://outlierkit.com/resources/youtube-api-quota/)
- [Watch Later / history change discussion](https://github.com/jdf76/plugin.video.youtube/issues/199)
- [Data API sample requests (older WL behaviour)](https://developers.google.com/youtube/v3/sample_requests)
- [yt-dlp: Windows Chrome cookie error message change (#11090)](https://gitea.smoothnet.org/nhyatt/yt-dlp/commit/b397a64691421ace5df09457c2a764821a2dc6f2)
- [App-bound encryption report](https://github.com/jim60105/YoutubeSegmentDownloader/issues/9)
- [Ongoing app-bound encryption status (2026 devlog)](https://stardance.hackclub.com/projects/3479/devlogs/23347)

---

## 4. Defects found in the original Phase 0 code

| # | Defect | Effect | Status |
| :--- | :--- | :--- | :--- |
| D1 | Re-ingesting overwrote `videos.category` | Every manual re-categorization was lost on the next sync | **Fixed**: `category_locked` column. Locked categories survive. |
| D2 | `videos.video_id` primary key with a single `playlist_id` | A video in two playlists "moved" to whichever synced last | **Fixed**: new `playlist_items` membership table, with auto-migration and backfill. |
| D3 | No record of videos removed from the source | Couldn't tell what you'd dropped on YouTube | **Fixed**: flagged `removed = 1`. They stay in the library and in totals until marked watched (see §5 S1, AC 1.6). |
| D4 | An empty or failed extraction could still save | Risk of an empty playlist record | **Fixed**: the pipeline stops when 0 videos are extracted. "Removed" is never marked from an empty result. |
| D5 | HTML report didn't escape titles or channels | Broken layout and script injection from a malicious video title | **Fixed**: `html.escape` in the static report. The dashboard escapes all user content. |
| D6 | Queue links truncated at 50 silently | Long categories couldn't be played fully | **Fixed**: chunked queue links. |
| D7 | `update_video_status` / `update_video_category` were unreachable | AC 4.4 had no UI | **Fixed**: dashboard. |
| D8 | Report built from in-memory classification, not the DB | Report ignored watch status and manual categories | **Fixed**: `main.py` reloads from the DB after saving. |
| D9 | Bare playlist IDs and `WL` were not accepted | Confusing errors | **Fixed**: `normalize_playlist_url`. |

The schema migration runs automatically on startup (`storage.init_db`) and was checked against a copy of the existing `yt_kb.db`.

---

## 5. Revised requirements (PRD v0.2)

### S1: Ingestion (revised)
- **AC 1.1′**: Ingest by yt-dlp metadata extraction. Access is public/unlisted, browser cookies (Firefox recommended), or `cookies.txt`. Accepts a URL, a bare ID, or `WL`.
- **AC 1.2′**: Cookies are never copied or stored by YT KB. Only the user's own file or browser profile is read.
- **AC 1.4′**: Unavailable entries are counted in the ingest log. Videos removed from the source are flagged, not deleted.
- **AC 1.5 (new)**: Re-sync preserves watch status, notes and manually set categories.
- **AC 1.6 (new): library retention policy.** Any number of playlists can be ingested. Each one is a *source* feeding one deduplicated library, so a video saved in two playlists has a single status. A video removed from a source on YouTube is **kept in the library, labelled "no longer in playlist", and still counted as remaining work until it is marked watched**. The library is the master list, and YouTube playlists are inboxes. Nothing is ever auto-deleted. A future "Archive / not interested" status could hide videos without marking them watched.

### S2: Classification (revised)
- **AC 2.1′**: The Phase 0 baseline is title plus channel keyword matching. Description and tags need one extra request per video (no longer "flat"). They are only worth it as input to the LLM classifier (§6 P1).
- **AC 2.3′**: Users add, rename and delete categories and edit keywords in the dashboard. "Re-run auto-classification" never overrides manual choices.
- **AC 2.5 (new)**: Every video shows whether its category is *manual* or *automatic*, and can be reset to automatic.

### S3: Progress & planning (new)
- **AC 3.1**: Status can be changed per video and in bulk, and remaining time updates immediately at the selected speed.
- **AC 3.2**: The Planner turns "minutes per day × days per week" into study days and an estimated finish date per category.
- **AC 3.3**: The Session builder fills a time budget with unwatched videos (playlist order, shortest, longest or in-progress first) and produces a playable queue.

### S4: Dashboard (new, replaces the static report as the primary UI)
- **AC 4.5**: A local-only web UI at `127.0.0.1` with Overview, Library, Category detail, Planner, Categories and Ingest pages, plus a video detail panel (embedded player and notes).
- **AC 4.6**: CSV and JSON export of the current playlist view.
- **AC 4.7**: The static `reports/` HTML and Markdown files remain as offline snapshots.

### S6: Write-back to YouTube (Phase 0.4b, deferred and redesigned)
- Opt-in per category, behind a dry-run screen showing *units needed vs. units left today*.
- A local job queue: create `[YT-KB] <Category>` playlists (50 units each), then insert videos (50 each), with a daily cap of about 9,000 units. The queue resumes the next day, and failures are recorded so they are not retried blindly.
- Write-back is idempotent: skip videos already in the target playlist (`playlistItems.list` costs 1 unit per 50).
- Only scope: `youtube` (needed to write). The source playlist ID is hard-blocked from every write call.

---

## 6. Recommended next steps (prioritized)

> **Status (2026-10-20):**
> - LLM classification with review: **done**. Providers: Claude, Gemini, OpenAI-compatible/open-weight, Ollama and LM Studio. The AI proposes categories that keep your presets, then suggests a category per video for you to accept.
> - Automated tests: **done** (`tests/`, 44 tests).
> - Live test against a private playlist: **done** for Watch Later (10/10 videos with durations, through the Chrome sign-in, auto-detected). A test on a 250+ video playlist is still open.

| Priority | Item | Why |
| :--- | :--- | :--- |
| **P1** | **LLM classification with review**: send batches of about 50 titles and channels to a small, fast model (e.g. Gemini Flash as the PRD proposes, or Claude Haiku). Ask it to assign one taxonomy category plus a confidence, or to *propose* clusters for the "General / Other" bucket. Results land as suggestions the user accepts in the dashboard. | Keyword matching misses anything without obvious keywords. That is the ≥ 88 % accuracy goal. |
| **P1** | **Live test against a real 250+ video private playlist** (Watch Later via Firefox cookies). | Validates AC 1.3, the duration coverage of flat extraction, and the bot-check risk. |
| **P1** | **Automated tests** for `metrics`, `classifier`, and the `storage` re-sync and migration rules. | These rules (locks, removal flags) are easy to regress. |
| P2 | Write-back queue (S6). | Only if queue links prove insufficient in practice. |
| P2 | Optional per-video enrichment (description and tags) used only by the LLM step. | Better classification without slowing basic ingestion. |
| P3 | Official captions or transcript summaries per video (the PRD allows captions, but not audio download). | Moves "knowledge base" from *organizing* to *learning*. That is the start of Phase 1. |

---

## 7. Dashboard map (what's built)

Run `python dashboard.py`, which opens `http://127.0.0.1:8765/`.

| Page | What you can do |
| :--- | :--- |
| **Library** (home) | One headline (time left at your speed), then a left rail to filter by status (To watch, In progress, Watched, Everything, Not in a playlist), by category and by playlist. The list supports search, sort, a one-click status circle, multi-select (mark, move, play) and chunked Play queues. |
| **Plan** | "I can watch N minutes a day, D days a week" gives finish dates per category, plus a session builder for today. |
| **Playlists** | Add or sync any number of playlists, choose a sign-in method, see the live log, and the list of sources with removed counts. |
| **Categories** | Rename, add and delete categories, edit keywords, save and re-sort. |
| **Video panel** | Embedded player, durations at every speed, status, category (manual or auto), notes, and which playlists the video is in. |

The global controls are the speed selector (remembered) and the theme toggle. The design is deliberately quiet: one accent colour, serif headlines, and list rows instead of cards.
Architecture: standard library `http.server` with a JSON API (`dashboard.py`), plus one static `web/index.html`. There are no new dependencies. The server accepts only localhost Host headers and requires a custom header on writes, so other websites can't drive it.

---

## 8. Decisions

### Answered (2026-10-20)

1. **Access method**: reading cookies from the browser is the default, and Firefox is recommended. It stays fresh on every run. A cookies.txt file goes stale because YouTube rotates session cookies, so it stays only as a fallback. On Linux, Chromium cookies also work.
2. **Category strategy**: categories are no longer a fixed list. An LLM derives them from the library, seeded by preset categories the user defines. The user reviews and approves the result. Keyword matching stays as the offline baseline.
3. **Sources**: many playlists will be ingested one at a time into the shared library (already supported). Write-back to YouTube is still undecided, so queue links remain the way to play videos for now.
4. **LLM provider**: users pick the provider in the dashboard. Options are Claude, Gemini, open-weight models through any OpenAI-compatible endpoint (OpenRouter, Groq, Together and others), and local models (Ollama, LM Studio). The LLM step is optional and runs after ingest.

### Original questions
These are the PRD §10 questions, updated:

1. **Access method**: use **Firefox cookies** (simplest) or a **cookies.txt export**? *Recommendation: Firefox, if you use it. Otherwise cookies.txt.*
2. **Category strategy**: *Recommendation: Hybrid.* Keep the editable keyword taxonomy as the fast baseline, and add the P1 LLM step that proposes categories for unmatched videos, which you approve in the dashboard.
3. **Write-back**: is playing via queue links enough, or do you want real `[YT-KB]` playlists on your account? The answer decides whether S6 is built in Phase 0 or dropped.
4. **LLM provider** for P1: Gemini Flash (per the PRD) or Claude. Either works. This choice affects API keys and cost.

---

## 9. Future: public release and user sign-in (deferred, decided 2026-10-20)

**Decision:** YT KB stays **local-first and single-user** for now. Each person runs their own copy, and it keeps reading YouTube through **browser cookies** (or `cookies.txt`). There is no user login, because nothing is shared: the database, cookies and API keys never leave the user's computer. Public release is deferred.

**Why not a hosted service with login:** a hosted version would have to hold every user's YouTube session cookies, which are as powerful as a password. It would also break Google's terms, and the official Data API cannot read Watch Later, the main use case. If a hosted or multi-user version is ever wanted, it needs Google OAuth plus the Data API (no Watch Later, 10k units/day quota) instead of cookies.

**Checklist for when we publish** (done 2026-10-20, repo audit found no secrets ever committed):
- [ ] Set the commit author to a GitHub noreply address and rewrite existing commits **before the first push**. They currently carry a personal email.
- [ ] Add a LICENSE (MIT suggested; compatible with yt-dlp and anthropic).
- [ ] README disclaimer: unofficial and not affiliated with YouTube or Google; uses yt-dlp, which YouTube changes can break; your own account at your own risk; read-only.
- [ ] `SECURITY.md`: how cookies and API keys are handled. Note that `settings.json` permissions aren't enforced on NTFS/FAT drives, so offer environment variables as the alternative.
- [ ] Dashboard access token, for shared machines: a random token in the URL, required on every `/api/*` call, on top of the existing Host and `X-YTKB` checks.
- [ ] Bundle fonts or use system fonts, so the dashboard works offline and makes no Google Fonts request.
- [ ] Remove notes about the author's own machine from `CLAUDE.md` and `docs/`.
- [ ] Install notes per OS: Linux secretstorage; Windows Chromium app-bound encryption; macOS keychain prompt.
- [ ] CI that runs `python -m unittest`.
- [ ] Make the cost of the default AI model clear (Claude Opus 5.5), or show an estimate before large runs.

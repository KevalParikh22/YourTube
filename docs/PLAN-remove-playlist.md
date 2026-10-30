# Plan: Remove a playlist from YourTube

**Status: built 2026-10-31 (branch feat/remove-playlist).** The open questions kept their assumptions: a kept video keeps its watched status; no trash. The confirm is the browser's own dialog, and the notes download happens whenever the playlist has notes (no checkbox). Kept in `docs/` (tracked) so it can be read on other systems.

**Goal:** the user can remove a playlist they no longer want to follow. It disappears from YourTube, along with the videos that came only from it and everything YourTube fetched or made for them. **Their notes are kept.**

**Branch (when started):** feat/remove-playlist

**Out of scope:**
- Anything on YouTube. The YouTube playlist and its videos are never touched (the project's hard rule). This only changes YourTube's local library.
- Removing single videos (a separate feature, if wanted later).

## The user's rule (2026-10-30)
- Remove the playlist **from YourTube, not from YouTube**.
- Remove its **videos and their subtitles and everything else** YourTube stored for them.
- **Never touch the notes.**

## Design notes

### Which videos are affected
- **In another playlist too:** untouched. Only the membership in the removed playlist goes. Its library may change, because a video's library follows its first remaining playlist (`storage._video_library`).
- **Only in this playlist, no notes:** removed completely. That means the `videos` row and every row YourTube keeps for it:
  - `playlist_items`;
  - `video_details` (description, chapters, caption status);
  - `transcript_segments` (captions and local transcripts);
  - `visual_segments` and the `frames/<video_id>/` thumbnails;
  - `media_jobs`;
  - `video_ratings`;
  - `video_summaries`;
  - `category_suggestions`;
  - `search_docs` (with the FTS index through its delete triggers) and orphaned `embedding_cache` rows.
- **Only in this playlist, with notes:** the notes stay. The `videos` row is kept, holding title, link, channel, duration, status and notes, and shown as "no longer in a playlist". Everything else listed above is removed, so its subtitles, transcripts, ratings, summaries and slides are gone. Its `notes` search row stays, so the notes can still be found.
- Knowledge pages (`topic_pages`) that cited removed videos are marked stale. They are rebuilt by the user as usual and never edited silently.
- `ask_history` keeps old answers as they were. Citations to removed videos show "video removed" instead of a link target.

### Flow
1. Playlists page: a **Remove** action on each playlist row.
2. A confirm dialog, filled by a dry run, says something like: "Remove *Watch later* from YourTube? 120 videos will be removed with their subtitles, ratings and summaries. 8 videos with your notes keep their notes. 15 videos are also in other playlists and stay. Nothing changes on YouTube."
3. Optional checkbox, on by default: **Download my notes first.** It uses the existing Markdown export (`exporter.markdown_zip`) for the affected videos.
4. Remove, then show the result line as a finished task.

### Code shape
- `storage.remove_playlist_plan(playlist_id) -> {removed, kept_notes, shared}`: counts only, no changes.
- `storage.remove_playlist(playlist_id) -> same counts`: one transaction.
  - Deletes in dependency order, then the `playlists` row.
  - Calls `storage._reindex` for every kept video whose searchable text changed.
  - Bumps the index version so the meaning index refreshes.
  - Deletes `frames/<id>/` folders after the commit.
  - Refuses a running sync of that playlist (`dashboard.Job` key).
- `GET /api/playlists/remove?id=` (dry run) and `POST /api/playlists/remove {id}`. The POST keeps the `X-YTKB` header, JSON body, Host and token checks. It runs through the `dashboard.Job` queue (job kind `remove`), because frames and index updates can take a while.
- Single-video "playlists" (*Added one by one*) can be removed the same way.

### Tests
- A shared video is untouched.
- A single-source video without notes is fully gone: every table above, the search index and its frames.
- A single-source video with notes keeps its row, notes and notes search, but loses its transcripts, ratings and summary.
- Other playlists and libraries are unaffected.
- The dry-run counts match the real removal.
- A playlist that is syncing is refused.

## Tasks (when the user approves)
- **T1** storage: `remove_playlist_plan`, `remove_playlist` and tests (`storage.py`, `tests/test_storage.py`).
- **T2** API and job: the dry run, the POST and tests (`dashboard.py`, `tests/test_dashboard.py`).
- **T3** UI: the Remove action, the confirm dialog with counts, the notes-download checkbox (`web/index.html`).
- **T4** Docs: `docs/SETUP.md` (Everyday use), `docs/MANUAL-TESTS.md` session, CLAUDE.md storage and API notes.

## Open questions for the user
- Should a removed video's **watched status** also be kept when it has notes? (Assumed yes: it lives on the same row as the notes.)
- Should there be an undo window, for example a 7-day trash, instead of removing straight away? (Assumed no: the confirm dialog plus the notes download is enough.)

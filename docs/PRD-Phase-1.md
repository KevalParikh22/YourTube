# PRD: Phase 1 — From organizing to learning

**Status:** Complete (2026-10-21). Built in steps 1–5 below. · **Builds on:** Phase 0 (ingest, categories, AI suggestions, planner, dashboard)

Phase 0 tells you *what* is in your backlog and *how long* it takes. Phase 1 tells you *what each video says*, so you can search it, triage it and learn from it without watching everything at 2×.

---

## 1. Goals

1. **Shrink the backlog honestly.** A short summary and a "worth watching?" note let you mark a video *skip* without watching it.
2. **Find things again.** Search across titles, descriptions, chapters, transcripts and your notes, and jump straight to the moment in the video.
3. **Better categories.** The AI classifier can use the description and the start of the transcript, not just the title.
4. **Keep the Phase 0 guarantees.** Local-first, read-only on YouTube, AI only suggests, nothing deleted.

**Success:** for a 250-video library, most talk/tutorial videos get a transcript; summaries cost a known, shown amount before running; search returns timestamped hits in under 1 s.

## 2. Non-goals (Phase 1)

- Downloading audio or video, or running speech-to-text (Whisper). Captions only, unless §7 decides otherwise.
- A chat that answers questions across the whole library (embeddings/RAG). That is Phase 2.
- Writing playlists back to YouTube (S6 stays deferred).
- Public release or multi-user login (see Phase 0 doc §9).

## 3. Findings that shape the design (tested 2026-10-20)

| Finding | Consequence |
|---|---|
| Flat playlist reads (Phase 0) work with cookies. | Unchanged. |
| A full per-video read **with** cookies fails ("The page needs to be reloaded"); **without** cookies it works in about 1.5 s and returns captions, chapters and description. | Video details are fetched **anonymously**. Playlists are private, but most videos in them are public. Private or members-only videos are marked "details unavailable". This also keeps signed-in traffic to a minimum. |
| yt-dlp warns that YouTube reads without a JavaScript runtime are deprecated. Node is installed here. | Pass `js_runtimes={'node': {}}` when Node or Deno is found, and say in the docs that one is recommended. |
| Many videos have auto captions (`en`, `<lang>-orig`). Music videos usually have none worth using. | Caption choice order: manual captions in the video's language, then auto `-orig`, then auto-translated English. Videos without captions are marked, not treated as errors. |
| Per-video requests add up (250 videos ≈ 250 requests). | A paced, resumable background queue with backoff, so bursts don't trigger YouTube's bot check. |

## 4. Features

### F1 — Video details ("enrich")
- Background queue that fetches per video: description, chapters, tags, the video's language and the chosen caption track. One request every few seconds, with exponential backoff on errors and a daily cap. Resumes after a restart.
- Runs on demand ("Fetch details for this category / these videos") and optionally after each sync for new videos.
- Status per video: `pending · done · no captions · unavailable · failed (n tries)`.

### F2 — Transcripts
- Store caption text with segment timestamps. Show a readable transcript in the video panel; clicking a line seeks the embedded player.
- Never published or exported in full: transcripts are for the owner's personal use only.

### F3 — Search
- SQLite FTS5 index over title, channel, description, chapters, transcript and notes.
- Results show the matching line and its timestamp, and open the video at that moment (`?start=`).

### F4 — AI summaries
- Per video: a two-line TL;DR, 3–7 key points with timestamps, and a **verdict** ("watch fully / skim chapters X–Y / skip, summary is enough") with a one-line reason.
- Uses the Phase 0 provider layer: Claude, Gemini, OpenAI-compatible or local.
- **Cost guardrail:** before a batch, show the number of videos, the estimated input tokens (transcript characters ÷ 4) and an approximate price for the selected model; it runs only after the user confirms. Results are cached and not regenerated unless asked.
- Long transcripts are split into chunks for small local models.

### F5 — Triage: AI watch ratings (revised 2026-10-20)
- **The AI rates every video; the user only confirms or overrides.** The user does not sort videos by hand.
- **Rating** per video:
  - a score from 0 to 10;
  - a verdict of **Watch**, **Skim** or **Skip**;
  - a one-line reason;
  - for Skim, the time ranges worth watching;
  - a TL;DR and key points with timestamps. This is the F4 summary, produced in the same call.
- **What the rating weighs:** content depth and quality (substance or hype), fit with the user's interests (categories, Keep presets and a free-text "My interests" setting), time against value (is a summary enough?), and freshness (penalize outdated tech, using the upload date).
- **Input:** transcript and chapters when available, otherwise title, channel, description and duration (marked as a "metadata-only" rating). Written in the user's preferred language.
- **Triage page = a ranked list:** thumbnail, score, verdict badge, reason and duration at the chosen speed. Filter chips for Watch / Skim / Skip / Unrated. **Accept all skips** applies the new **Skipped** status in bulk, and every change can be undone. The header compares watch time for "only the Watch videos" with "everything".
- **Skipped** status (done in step 1): counts as done for remaining time and never deletes anything.
- **Cost guardrail:** before a rating batch, show the number of videos, estimated input tokens and approximate price for the chosen model.

### F8 — AI per task (added 2026-10-20)
- The user configures any number of providers (cloud or local) and picks which one does each job: a **default**, **categories** (propose and suggest) and **ratings and summaries**. Each job falls back to the default.

### F6 — Smarter AI categories
- The Phase 0 suggest step can include the description and the first ~1,000 characters of the transcript, when details are available.

### F7 — Export notes
- Markdown export per video or category (title, link, your notes, summary, key points with timestamped links). Works with Obsidian and other note apps. Full transcripts are not included.

## 5. Data model (additions)

- `video_details(video_id PK, description, tags JSON, chapters JSON, language, caption_lang, caption_kind, status, tries, fetched_at, error)`
- `transcript_segments(video_id, start_s, end_s, text)` plus FTS5 table `search_index`
- `summaries(video_id PK, tldr, points JSON, verdict, reason, model, created_at)`
- `videos.status` gains `skipped`. The CHECK constraint is rebuilt in a migration, and metrics treat `skipped` like `completed` for remaining time.

## 6. Build order (proposed)

1. Skipped status (done). The first manual Triage missed the requirement and is replaced by step 3.
2. Details queue and transcripts (F1, F2). **Done 2026-10-20.** Tested on the real library: 3/3 real videos got transcripts (283–3,190 lines) and chapters, and 5 placeholder IDs were marked unavailable. Takes about 5 s per video.
3. **AI watch ratings and summaries (F4 + F5 revised), AI per task (F8), and a more visual UI** (thumbnails, verdict colors, better narrow-window layout). **Done 2026-10-20.** Tested end to end with a stand-in model on a copy of the library. Not yet run against a real AI.
4. Search (F3). **Done 2026-10-20.**
   - SQLite FTS5 with the **trigram** tokenizer, so it matches parts of words in any script (Hindi included); terms need 3+ characters.
   - Indexed: titles and channels, descriptions, chapters, transcripts in ~30 s blocks, notes and AI summaries and key points. The index updates on every change.
   - The whole real library indexes in 0.2 s.
   - The Search page, the header box and "/" show results grouped by video. Each hit has a kind label, its time and a highlighted ~200-character snippet, and clicking it opens the video at that moment.
5. Smarter categories (F6), then Markdown export (F7). **Done 2026-10-21. Phase 1 complete.**
   - F6: category suggestions send each video's AI summary, the first ~300 characters of its description and the first ~1,000 characters of its transcript. Batches are packed to fit the model's context (smaller for local models). Real run with `qwen3.5:4b`: 8 videos in one batch, 22 s.
   - F7: one note per video with Obsidian front matter, verdict, summary, key points, skim ranges and chapters as timestamped links, and the user's notes. No full transcripts. Downloads: one note from the video panel, or a zip (`YT KB/<Category>/<Title>.md` plus a category index note) for the current Library view or the selected videos. CSV and JSON export are now in the Library's Export menu too.

Each step ships with tests and keeps the dashboard working.

## 7. Decisions (2026-10-20)

1. **Transcripts:** fetched for **all videos except music**. "Music" means categories marked as music (a per-category "No transcripts" switch, on by default for names containing music/songs/mix/bhajan/lofi) and videos YouTube labels as Music. Videos without captions are marked, not retried.
2. **Summaries:** **on demand and in batches.** One video from its panel, or a batch for a category or selection after the estimated cost is shown and confirmed. Never automatic.
3. **Summary language:** **the user's preferred language**, a setting on the AI page (default English). Summaries, key points and verdicts are written in that language, whatever language the video is in.
4. **No captions:** marked **"no captions"**. Phase 1 stays caption-only; local speech-to-text is not planned.

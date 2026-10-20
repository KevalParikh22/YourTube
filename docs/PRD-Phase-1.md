# PRD: Phase 1 — From organizing to learning

**Status:** v0.3: local transcription and visual processing added (2026-10-20) · **Builds on:** Phase 0 (ingest, categories, AI suggestions, planner, dashboard)

Phase 0 tells you *what* is in your backlog and *how long* it takes. Phase 1 tells you *what each video says*, so you can search it, triage it and learn from it without watching everything at 2×.

---

## 1. Goals

1. **Shrink the backlog honestly.** A short summary and a "worth watching?" note let you mark a video *skip* without watching it.
2. **Find things again.** Search across titles, descriptions, chapters, transcripts and your notes, and jump straight to the moment in the video.
3. **Better categories.** The AI classifier can use the description and the start of the transcript, not just the title.
4. **No video left blank.** If a video has no captions, transcribe its audio locally. If the content lives on screen (slides, whiteboard, code), read the frames locally.
5. **Keep the Phase 0 guarantees.** Local-first, read-only on YouTube, AI only suggests, nothing deleted from YouTube or the library.

**Success:** for a 250-video library, most talk/tutorial videos get a transcript; summaries cost a known, shown amount before running; search returns timestamped hits in under 1 s.

## 2. Non-goals (Phase 1)

- Keeping downloaded media. Audio and video are fetched only as temporary working files for local processing (F8, F9), deleted afterwards, and never shared or exported.
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
| Anonymous reads (with Node as the JS runtime) list **audio-only** streams. The smallest is about 49 kbps m4a or webm: about **5 MB for a 14-minute video, roughly 20 MB per hour**. They also list **video-only** streams from 144p to 2160p. | Audio for transcription is cheap to fetch. Slides are readable at 480p–720p video-only (no audio track needed). Still to test: an actual download, anonymously and at a paced rate. |
| This machine: NVIDIA RTX 3050 Laptop (**4 GB VRAM**), 14 GB RAM (about 5 GB free in normal use), 16 CPU threads, `ffmpeg` installed, 63 GB free on the project drive. Ollama and an OCR engine are not installed yet. | Pick models that fit in 4 GB VRAM. Run **one GPU job at a time** (speech-to-text, vision model and LLM never load together). Fall back to CPU when no GPU is present. Cap and clean the media cache. |

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

### F5 — Triage
- A new status, **Skipped** ("not interested"). It counts as done for remaining time, can be undone, and never deletes anything (closes the "Archive" idea from Phase 0 AC 1.6).
- A Triage view that steps through unwatched videos one at a time: summary and verdict, then **Watch** / **Skim** / **Skip**.

### F6 — Smarter AI categories
- The Phase 0 suggest step can include the description and the first ~1,000 characters of the transcript, when details are available.

### F7 — Export notes
- Markdown export per video or category (title, link, your notes, summary, key points with timestamped links). Works with Obsidian and other note apps. Full transcripts are not included.

### F8 — Local transcription (no captions? listen locally)
- **When:** a non-music video has no usable captions. Optionally also when the only captions are a poor auto-translation (setting).
- **How:** download **audio only** at the smallest quality (anonymously, through the same paced queue). Convert it to 16 kHz mono with `ffmpeg`. Transcribe locally with **faster-whisper**: a large-v3-turbo-class model in int8 on the GPU, or a smaller model on the CPU. The language is detected automatically, including Hindi and Hinglish, with an option to translate to English. Output is timestamped segments, stored like captions, with `source = 'local-asr'`.
- **Afterwards:** the audio file is **deleted** as soon as the transcript is saved.
- **Shown to the user:** a queue with per-video state (`downloading → transcribing → done / failed`) and an estimated time from the audio length and measured speed. Nothing runs until the user starts it, per video, per category, or "all without captions".

### F9 — Visual content (slides, whiteboard, code on screen)
- **Which videos:** opt-in. Mark a video or category as **"Has slides / whiteboard"**. The app also *suggests* the mark from hints: title words like lecture, slides, presentation, whiteboard or walkthrough; few spoken words per minute; or a transcript that keeps referring to "this slide" or "as you can see".
- **How:**
  1. Download **video only**, no audio, at 480p (720p for small text, as a setting).
  2. Use `ffmpeg` scene-change detection to pick frames where the picture changes, at most one every ~10 s and at most ~150 per video.
  3. Drop near-duplicate frames with a perceptual hash.
  4. Read each kept frame **locally**: OCR for slide and code text, and/or a **small local vision model** (through Ollama) that describes diagrams and whiteboard drawings in one or two sentences.
- **Output:** timestamped "visual segments" (`source = 'ocr' | 'vision'`) stored next to the transcript. They feed search (F3) and summaries (F4), so a summary can say "slide at 12:40 lists the 5 steps". Small JPEG thumbnails of key frames can be kept (setting) for a slide strip in the video panel.
- **Afterwards:** the video file is **deleted** after processing. Only text and, if chosen, thumbnails are kept.

### F10 — Local AI by default
- **One local runtime: Ollama.** It serves the text LLM (summaries, categories) and the vision model (F9) behind the existing OpenAI-compatible provider. faster-whisper runs in-process for speech. Cloud providers (Claude, Gemini, OpenAI-compatible) remain optional, per task.
- **Per-task model choice** on the AI page: *categories*, *summaries*, *vision*, *speech*. A "local only" switch blocks every cloud call.
- **Hardware fit (4 GB VRAM, 14 GB RAM):** a 3–4B text model in Q4 fully on the GPU, or a 7–8B model split between GPU and CPU (slower). A 3–4B vision model. A turbo-class Whisper in int8. One model loaded at a time; the app tells Ollama to unload (`keep_alive: 0`) before speech-to-text runs.
- **Setup check:** the AI page shows what is installed (ffmpeg, Ollama and pulled models, faster-whisper, CUDA or CPU) and what to install next.

### Resource and safety rules for F8–F10
- **Media cache:** a single folder (git-ignored) with a size cap (default 2 GB). Files are deleted after each job and cleaned at startup. Nothing is ever written next to the user's data.
- **One heavy job at a time:** downloads are paced. GPU and CPU work runs serially. Jobs pause or resume and survive a restart. Each job stores its status, tries, error and timings.
- **Read-only on YouTube, anonymous requests.** Cookies are never used for media downloads.
- **Personal use only.** Downloading may be restricted by YouTube's terms. YT KB keeps media only temporarily, for processing on the user's own computer, never redistributes it, and says so in the UI and docs. The user chooses to run it.

## 5. Data model (additions)

- `video_details(video_id PK, description, tags JSON, chapters JSON, language, caption_lang, caption_kind, status, tries, fetched_at, error)`
- `transcript_segments(video_id, start_s, end_s, text)` plus FTS5 table `search_index`
- `summaries(video_id PK, tldr, points JSON, verdict, reason, model, created_at)`
- `transcript_segments` gains `source` (`captions | local-asr`). A new table `visual_segments(video_id, t_s, kind ('ocr'|'vision'), text, thumb_path)`, also indexed in `search_index`.
- `media_jobs(id, video_id, kind ('details'|'audio-asr'|'visual'), status, tries, error, progress, started_at, finished_at, timings JSON)`: one table drives the resumable queue for F1, F8 and F9.
- `videos.has_visuals` (null = unknown, 0/1 = set by the user, or suggested and accepted). Categories get a "has visuals" default and a "no transcripts" (music) switch.
- `videos.status` gains `skipped`. The CHECK constraint is rebuilt in a migration, and metrics treat `skipped` like `completed` for remaining time.

## 6. Build order (proposed)

1. Skipped status and Triage (small, immediately useful). **Done 2026-10-20.**
2. Details queue and transcripts (F1, F2).
3. Search (F3).
4. Summaries with cost guardrail (F4), then triage verdicts.
5. Smarter categories (F6), then Markdown export (F7).
6. **Spike (before 7–8):** on 3–5 real videos, check that an anonymous paced audio and video download works. Measure faster-whisper speed and VRAM on the RTX 3050. Compare OCR with a small vision model on slide frames. Record the numbers here.
7. Local transcription (F8) on the shared `media_jobs` queue, plus the setup check (F10).
8. Visual content (F9): frames, dedupe, OCR and vision, then visual segments in search and summaries.

Step 2's details queue should be built on the `media_jobs` design, so that F8 and F9 later add new job kinds instead of a second queue.

Each step ships with tests and keeps the dashboard working.

## 7. Decisions (2026-10-20)

1. **Transcripts:** fetched for **all videos except music**. "Music" means categories marked as music (a per-category "No transcripts" switch, on by default for names containing music/songs/mix/bhajan/lofi) and videos YouTube labels as Music. Videos without captions are marked, not retried.
2. **Summaries:** **on demand and in batches.** One video from its panel, or a batch for a category or selection after the estimated cost is shown and confirmed. Never automatic.
3. **Summary language:** **the user's preferred language**, a setting on the AI page (default English). Summaries, key points and verdicts are written in that language, whatever language the video is in.
4. ~~**No captions:** marked "no captions"; caption-only.~~ **Revised 2026-10-20:** videos without captions are **transcribed locally** from temporarily downloaded audio (F8). Videos whose content is on screen (slides, whiteboard, code) get **local frame processing** (F9). **Local LLMs are the default** for the heavy work (F10), and cloud models stay optional. Downloaded media is temporary and deleted after processing.

# PRD: Phase 2.5 — Everything processed locally

**Status:** Draft v0.1 (2026-10-20) · **Builds on:** Phase 1 (details queue, transcripts, search, summaries, triage) and Phase 2 (library-wide Q&A)

Phase 1 learns from videos that have captions, using any AI provider. Phase 2.5 makes sure **no video is left blank**, and that the heavy work can run **entirely on the user's own computer**:
- videos without captions are transcribed from their audio;
- videos whose content is on screen (slides, whiteboard, code) are read frame by frame;
- local models do the AI work by default.

---

## 1. Goals

1. **No captions? Listen locally.** Transcribe the audio on this computer.
2. **Content on screen? Read it locally.** Extract slides, whiteboard drawings and on-screen code as timestamped text.
3. **Local AI by default.** Summaries, categories and vision run on local models. Cloud providers stay optional, and a "local only" switch blocks them entirely.
4. **Keep every earlier guarantee.** Read-only on YouTube, AI only suggests, nothing deleted from YouTube or the library.

## 2. Non-goals

- Keeping downloaded media. Audio and video are only temporary working files, deleted after processing, never shared or exported.
- Training or fine-tuning models.
- Cloud transcription or vision services. Everything in this phase is local; cloud remains an option only through the existing Phase 0/1 provider layer.

## 3. Findings (tested 2026-10-20)

| Finding | Consequence |
|---|---|
| Anonymous reads (with Node as the JS runtime) list **audio-only** streams. The smallest is about 49 kbps m4a or webm: about **5 MB for a 14-minute video, roughly 20 MB per hour**. They also list **video-only** streams from 144p to 2160p. | Audio for transcription is cheap to fetch. Slides are readable at 480p–720p video-only (no audio track needed). Still to test: an actual download, anonymously and at a paced rate. |
| A per-video read **with** cookies fails ("The page needs to be reloaded"), while anonymous reads work. | Media downloads are **anonymous**; cookies are never used for them. |
| This machine: NVIDIA RTX 3050 Laptop (**4 GB VRAM**), 14 GB RAM (about 5 GB free in normal use), 16 CPU threads, `ffmpeg` installed, 63 GB free on the project drive. Ollama and an OCR engine are not installed yet. | Pick models that fit in 4 GB VRAM. Run **one GPU job at a time**: speech-to-text, vision model and LLM never load together. Fall back to CPU when no GPU is present. Cap and clean the media cache. |

## 4. Features

### L1 — Local transcription (no captions? listen locally)
- **When:** a non-music video has no usable captions. Optionally also when the only captions are a poor auto-translation (setting).
- **How:** download **audio only** at the smallest quality (anonymously, through the paced job queue). Convert it to 16 kHz mono with `ffmpeg`. Transcribe locally with **faster-whisper**: a large-v3-turbo-class model in int8 on the GPU, or a smaller model on the CPU. The language is detected automatically, including Hindi and Hinglish, with an option to translate to English. Output is timestamped segments, stored like captions, with `source = 'local-asr'`.
- **Afterwards:** the audio file is **deleted** as soon as the transcript is saved.
- **Shown to the user:** a queue with per-video state (`downloading → transcribing → done / failed`) and an estimated time from the audio length and measured speed. Nothing runs until the user starts it, per video, per category, or "all without captions".

### L2 — Visual content (slides, whiteboard, code on screen)
- **Which videos:** opt-in. Mark a video or category as **"Has slides / whiteboard"**. The app also *suggests* the mark from hints: title words like lecture, slides, presentation, whiteboard or walkthrough; few spoken words per minute; or a transcript that keeps referring to "this slide" or "as you can see".
- **How:**
  1. Download **video only**, no audio, at 480p (720p for small text, as a setting).
  2. Use `ffmpeg` scene-change detection to pick frames where the picture changes, at most one every ~10 s and at most ~150 per video.
  3. Drop near-duplicate frames with a perceptual hash.
  4. Read each kept frame **locally**: OCR for slide and code text, and/or a **small local vision model** (through Ollama) that describes diagrams and whiteboard drawings in one or two sentences.
- **Output:** timestamped "visual segments" (`source = 'ocr' | 'vision'`) stored next to the transcript. They feed search and summaries, so a summary can say "slide at 12:40 lists the 5 steps". Small JPEG thumbnails of key frames can be kept (setting) for a slide strip in the video panel.
- **Afterwards:** the video file is **deleted** after processing. Only text and, if chosen, thumbnails are kept.

### L3 — Local AI by default
- **One local runtime: Ollama.** It serves the text LLM (summaries, categories, Q&A) and the vision model (L2) behind the existing OpenAI-compatible provider. faster-whisper runs in-process for speech.
- **Per-task model choice** on the AI page: *categories*, *summaries*, *vision*, *speech*. A "local only" switch blocks every cloud call.
- **Hardware fit (4 GB VRAM, 14 GB RAM):** a 3–4B text model in Q4 fully on the GPU, or a 7–8B model split between GPU and CPU (slower). A 3–4B vision model. A turbo-class Whisper in int8. One model loaded at a time; the app tells Ollama to unload (`keep_alive: 0`) before speech-to-text runs.
- **Setup check:** the AI page shows what is installed (ffmpeg, Ollama and pulled models, faster-whisper, CUDA or CPU) and what to install next.

## 5. Resource and safety rules

- **Media cache:** a single folder (git-ignored) with a size cap (default 2 GB). Files are deleted after each job and cleaned at startup. Nothing is ever written next to the user's data.
- **One heavy job at a time:** downloads are paced. GPU and CPU work runs serially. Jobs pause or resume and survive a restart. Each job stores its status, tries, error and timings.
- **Read-only on YouTube, anonymous requests.** Cookies are never used for media downloads.
- **Personal use only.** Downloading may be restricted by YouTube's terms. YT KB keeps media only temporarily, for processing on the user's own computer, never redistributes it, and says so in the UI and docs. The user chooses to run it.

## 6. Data model (additions)

- `transcript_segments` (from Phase 1) gains `source` (`captions | local-asr`).
- `visual_segments(video_id, t_s, kind ('ocr'|'vision'), text, thumb_path)`, also indexed in the Phase 1 search index.
- `media_jobs(id, video_id, kind ('details'|'audio-asr'|'visual'), status, tries, error, progress, started_at, finished_at, timings JSON)`: one table drives the resumable queue. Ideally Phase 1's details queue already uses this shape, and Phase 2.5 only adds new job kinds.
- `videos.has_visuals` (null = unknown, 0/1 = set by the user, or suggested and accepted). Categories get a "has visuals" default.

## 7. Build order

1. **Spike:** on 3–5 real videos, check that an anonymous paced audio and video download works. Measure faster-whisper speed and VRAM on the RTX 3050. Compare OCR with a small vision model on slide frames. Record the numbers in §3.
2. Setup check and per-task local models (L3). **Done 2026-10-20**, except that the *vision* and *speech* task choices arrive with L2 and L1:
   - Native Ollama provider (`/api/chat` with `num_ctx`, JSON format and `keep_alive`). The OpenAI-compatible `/v1` path can't set the context size, so long transcripts would be cut silently.
   - Setup check on the AI page (GPU, memory, ffmpeg, Ollama and its models, faster-whisper), with one-click model download and "Use".
   - "Local only" switch, and model recommendations by GPU memory (4 GB → `qwen3.5:4b`, 3.3 GB, text and images).
   - Setup scripts for users: `scripts/setup.sh [--local-ai]` (Linux/macOS, tested on Linux) and `scripts/setup.ps1 [-LocalAI]` (Windows, not yet tested).
   - Not yet tested against a real Ollama: Ollama was not installed when this was built.
3. Local transcription (L1) on the shared job queue.
4. Visual content (L2): frames, dedupe, OCR and vision, then visual segments in search and summaries.

Each step ships with tests and keeps the dashboard working.

## 8. Open decisions

1. **OCR engine vs. vision model:** decided by the spike's results on real slide frames.
2. **Default Whisper model:** turbo-class on GPU vs. smaller on CPU, decided by measured speed.
3. **Thumbnails:** keep key-frame thumbnails for a slide strip, or text only?

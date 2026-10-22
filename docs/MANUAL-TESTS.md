# Manual tests (human checks)

These need your eyes, your YouTube account or your hardware. Start the dashboard with `.venv/bin/python dashboard.py`. For each test, note **what you saw** in the "Result" column, especially anything confusing, wrong or slow, then share it.

## 1. Library at real size

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| 1.1 | Big playlist sync | Playlists → paste a 250+ video playlist (or `WL`) → Detect my browser login → Sync | All videos arrive with durations; it finishes in about a minute or two | |
| 1.2 | Several playlists | Sync 2–3 different playlists, including one that shares videos with another | Shared videos appear once; the Playlists table shows each source | |
| 1.3 | Fetch details at scale | Playlists → Fetch details (200 per run) | Steady progress, about 5 s per video; if YouTube shows a bot check, it stops cleanly and a later run continues | |
| 1.4 | Search at scale | Search a few topics you know are in the videos | Relevant videos and the right moments; clicking a match jumps there | |

## 2. AI quality (local model)

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| 2.1 | Interests | AI → About you → write what you want to learn and what you don't → Save | Saved | |
| 2.2 | Ratings spread | Triage → Rate (on a mixed playlist, after Fetch details) | A mix of Watch / Skim / Skip, not everything 8–9 | |
| 2.3 | Skip verdicts | Read 5–10 Skip reasons, and open a couple of the videos | You agree they're skippable; note any you disagree with | |
| 2.4 | Skim ranges | Click a Skim range on a few videos | It lands on the part the reason describes | |
| 2.5 | Categories | Categories → Propose categories with AI → review → Apply; then Sort unsorted with AI → review suggestions | Sensible categories; suggestions mostly right; note the wrong ones | |
| 2.6 | Pending suggestion | Library → AI suggestions → Graph RAG → Accept or Dismiss | Your call | |

## 3. Local media (Phase 2.5)

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| 3.1 | Local transcription | Playlists → Local transcription → Transcribe (for videos without captions or with wrong-script captions) | English (or the right language) transcripts; about 2.5 min per hour of audio on the GPU | |
| 3.2 | Wrong-script decision | For transcribed videos that also have captions, compare both in the panel (switch) | Decide: should the local transcript be used **automatically** for wrong-script captions? | |
| 3.3 | Slides | Mark 3–5 slide-heavy videos (or accept the suggestions) → Read slides | The strip shows real slides and code, not talking heads; the on-screen text is mostly correct | |
| 3.4 | Laptop heat | During longer runs, watch temperature and noise | Pauses kick in when hot; the laptop stays usable | |

## 4. Setup and housekeeping

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| 4.1 | Setup guide | Read the Setup page as if you were a new user | Clear; note anything missing or confusing | |
| 4.2 | Windows script | On a Windows PC (if available): `scripts\setup.ps1 -LocalAI` | It sets up the app; report any error | |
| 4.3 | Test data | Decide: remove the "Test Engineering Playlist" (`v1`–`v5`)? | Your call (removing it needs a one-off cleanup that you approve) | |
| 4.4 | Write-back | Decide: create real `[YT-KB]` playlists on your YouTube account, or keep queue links? | Your call | |
| 4.5 | Notes export | Library → Export → Notes; open the zip in Obsidian or any Markdown app | Readable notes; timestamp links open the right moment | |

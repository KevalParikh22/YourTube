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
| 2.1 | Interests | Settings → General → About you → write what you want to learn and what you don't → Save | Saved | |
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
| 4.1 | Setup guide | Read the Settings → Setup and help as if you were a new user | Clear; note anything missing or confusing | |
| 4.2 | Windows script | On a Windows PC (if available): `scripts\setup.ps1 -LocalAI` | It sets up the app; report any error | |
| 4.3 | Test data | Decide: remove the "Test Engineering Playlist" (`v1`–`v5`)? | Your call (removing it needs a one-off cleanup that you approve) | |
| 4.4 | Write-back | Decide: create real `[YourTube]` playlists on your YouTube account, or keep queue links? | Your call | |
| 4.5 | Notes export | Library → Export → Notes; open the zip in Obsidian or any Markdown app | Readable notes; timestamp links open the right moment | |

## 5. Cloud AI with your own keys

Local models are tested; these check the cloud providers. Use cheap models first (Claude Haiku, Gemini Flash-Lite). The automated part runs only when you give it keys, and never saves them:

```bash
YTKB_LIVE=1 ANTHROPIC_API_KEY=... GEMINI_API_KEY=... .venv/bin/python -m unittest tests.test_live -v
```

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| 5.1 | Automated live check | Run the command above with the keys you have (optionally `OPENAI_API_KEY` + `YTKB_LIVE_OPENAI_MODEL` for an OpenRouter model) | 4 tests pass per provider: connection, categories, a rating, an answer with a correct citation | |
| 5.2 | Add a key | AI → Your AIs → Claude (or Gemini) → paste key → Save and test | "Works"; after a reload the page says *A key is saved* and never shows the key | |
| 5.3 | Key from environment | Remove the saved key (Clear), start the dashboard with `ANTHROPIC_API_KEY=... .venv/bin/python dashboard.py` | The page says it uses the key from your environment | |
| 5.4 | Wrong key | Save an invalid key → Save and test | A clear error, nothing crashes | |
| 5.5 | Mixed routing | Which AI does what: Categories = local, Ratings = cloud, Questions = same as ratings | Each job uses the AI shown; Ask's hint names the cloud AI | |
| 5.6 | Cost estimate | Triage → Rate a few videos with the cloud AI | The estimate appears before it starts and is in line with what the provider's billing page shows afterwards | |
| 5.7 | Cloud quality | Rate the same 5 videos with local and cloud; ask the same 3 questions with both | Note which is better: score spread, Skip reasons, answer accuracy and citations | |
| 5.8 | Cloud categories | Categories → Propose with AI and Sort unsorted with the cloud AI | Sensible results; compare with local | |
| 5.9 | Local only | AI → tick Local only | Cloud AIs show as blocked; rating or asking with a cloud AI gives a clear message; nothing is sent | |

## 6. Ask your library (Phase 2)

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| 6.1 | Questions | Ask → 5 questions on topics you know are in your videos (one in Hindi or another language) | Correct short answers, or "not covered" when they aren't | |
| 6.2 | Citations | Click the numbers in a few answers | Each opens the video at the moment that supports the sentence | |
| 6.3 | Follow-ups | Ask a follow-up like "and where does he show the code?" | "Searched for" makes sense; the answer continues the topic | |
| 6.4 | One video | Open a video → Ask about this video | Answers come only from that video | |
| 6.5 | Search by meaning | Search → tick By meaning → search an idea in other words | The right videos near the top, even without the same words | |
| 6.6 | Index status | AI → Local AI → Meaning-based search | Shows all pieces indexed; Update index and Rebuild work and show progress | |
| 6.7 | History | Reopen, filter, export and delete earlier questions | Works; the exported note's links open the right moments | |

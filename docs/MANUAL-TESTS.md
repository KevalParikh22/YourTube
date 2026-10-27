# Manual tests (human checks)

These need your eyes, your YouTube account or your hardware. Start the dashboard with `.venv/bin/python dashboard.py`. For each test, note **what you saw** in the "Result" column, especially anything confusing, wrong or slow, then share it.

## How to take the rest: short sessions

Section 1 is done (2026-10-24: all working). Do the rest as sessions, one at a time, in this order. Each needs only the dashboard open. At the end of a session, paste your notes in the chat, even rough ones; fixes come in batches.

| Session | Covers | Time | Before you start |
|---|---|---|---|
| **S1 · Ratings you can trust** | §2 (2.1–2.4), §7.6, §7.7, and S1 below | ~30 min | Settings → General → About you: write your interests. Ratings changed on 2026-10-24 (score breakdown, minutes), so re-rate 10–20 videos first: Triage → Rate |
| **S2 · Libraries and the task queue** | §7.1–7.5, §2.5–2.6 | ~20 min | Nothing |
| **S3 · Ask your library** | §6 | ~20 min | Ollama running (Settings → AI shows it) |
| **S4 · Local media** | §3 | ~30 min + waiting | Pick 3–5 videos without captions and 3–5 with slides; keep the laptop on power |
| **S5 · Cloud AI** | §5 | ~20 min | An API key (Claude or Gemini). Optional; skip if you only use local AI |
| **S6 · Setup and decisions** | §4 | ~15 min | Nothing |
| **S7 · Summaries and knowledge (Phase 3)** | S7 below | ~40 min + waiting | Ollama running; pick 4–5 videos of one category you know well |

### S1 extra checks (new ratings, 2026-10-24)

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| S1.1 | Why this score | Open a rated video → "Why this score" | Four factors with reasons you agree with; the formula line adds up to the score | |
| S1.2 | Minutes | Read the Minutes of 3 videos you have watched | You could decide watch / skip from them alone; timestamps jump to the right part | |
| S1.3 | Off-interest video | Find a good video outside your interests | It still scores well (interest only subtracts up to 2) | |
| S1.4 | Your formula | Settings → General → How scores are calculated: change the interest penalty, save, re-rate one video | The new number follows the formula; Reset to default works | |
| S1.5 | Stop | Start Sort unsorted with AI, then Stop during batch 2 | It says Stopping…, ends before the next batch, and keeps the suggestions made so far | |
| S1.6 | Look and feel | Browse every page in light and dark | Clean fonts, styled dropdowns, tooltips (ⓘ) instead of paragraphs, readable categories; note anything still rough | |

### S7 checks (summaries and knowledge pages, 2026-10-28)

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| S7.1 | Summarise | Open a video you have watched → Summarise (1–2 min) | TL;DR, minutes, claims and quotes, questions, takeaways; accurate to what was said; timestamps land right | |
| S7.2 | Better than YouTube | Compare with YouTube's own summary of the same video | Ours is more useful for deciding and for revising; note what is missing | |
| S7.3 | Check yourself | Answer the questions before opening the answers | Questions test the important ideas; answers and times are right | |
| S7.4 | Related moments | Look at "Also in" chips under the minutes | They point to genuinely related parts of your other videos | |
| S7.5 | Learn more | Read the related videos and YouTube searches | Sensible prerequisites / deeper / other views; the searches fill real gaps | |
| S7.6 | Knowledge page | Summarise 4–5 videos of one category → Knowledge → Build → Open | A page you could study from; every point's chips open the right moment; Export note works in Obsidian | |
| S7.7 | Stale page | Summarise one more video of that category | The page shows Update; Update includes it | |
| S7.8 | Description | Open a video; then Settings → General → Show descriptions | Collapsed by default; open when the setting is on | |

## 1. Library at real size ✓ done 2026-10-24

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
| 3.2 | Wrong-script (decided 2026-10-25: local automatically) | For transcribed videos that also have captions, compare both in the panel (switch) | Decide: should the local transcript be used **automatically** for wrong-script captions? | |
| 3.3 | Slides | Mark 3–5 slide-heavy videos (or accept the suggestions) → Read slides | The strip shows real slides and code, not talking heads; the on-screen text is mostly correct | |
| 3.4 | Laptop heat | During longer runs, watch temperature and noise | Pauses kick in when hot; the laptop stays usable | |

## 4. Setup and housekeeping

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| 4.1 | Setup guide | Read the Settings → Setup and help as if you were a new user | Clear; note anything missing or confusing | |
| 4.2 | Windows script | On a Windows PC (if available): `scripts\setup.ps1 -LocalAI` | It sets up the app; report any error | |
| 4.3 | Test data (done 2026-10-25: removed) | Decide: remove the "Test Engineering Playlist" (`v1`–`v5`)? | Your call (removing it needs a one-off cleanup that you approve) | |
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

## 7. Libraries, queue and settings (feedback round, 2026-10-23) · session S2

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| 7.1 | Create a library | Playlists → Libraries → type "Health" → Create; move one or two playlists to it with the Library column | The header switcher shows Health; Library, Plan, Triage, Search and Ask show only its videos | |
| 7.2 | Its own categories | In Health: Categories → Propose categories with AI → Apply; set Health interests in Settings → General | Health categories don't appear in My library; ratings in Health judge against Health interests | |
| 7.3 | Queue | Start a transcription, then click Rate | Rate says "queued"; the header shows the running task and "+1 waiting"; Rate runs after | |
| 7.4 | Finished state | Let a sync finish | The header turns green "Finished: …" and the page shows a Finished banner; a failure shows red with the error | |
| 7.5 | Single video | Playlists → paste one video link → Sync | It appears in *Added one by one*, with details fetched | |
| 7.6 | Search two words | Search "saying no" (and a phrase of your own) | Videos with the exact phrase first, then "With the words apart" | |
| 7.7 | Skim | Rate a few long videos; click ▶ Skim | It starts at a useful part, not the intro | |

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

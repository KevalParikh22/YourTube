# Manual tests (human checks)

These need your eyes, your YouTube account or your hardware. Start the dashboard with `.venv/bin/python dashboard.py`. For each test, note **what you saw** in the "Result" column, especially anything confusing, wrong or slow, then share it.

## How to take the rest: short sessions

Section 1 is done (2026-10-24: all working). Do the rest as sessions, one at a time, in this order. Each needs only the dashboard open. At the end of a session, paste your notes in the chat, even rough ones; fixes come in batches.

| Session | Covers | Time | Before you start |
|---|---|---|---|
| **S1 · Ratings you can trust** | §2 (2.1–2.4), §7.6, §7.7, and S1 below | ~30 min | Settings → About you: write your interests. Ratings changed on 2026-10-24 (score breakdown, minutes), so re-rate 10–20 videos first: Library → Rate |
| **S2 · Libraries and the task queue** | §7.1–7.5, §2.5–2.6 | ~20 min | Nothing |
| **S3 · Ask your library** | §6 | ~20 min | Ollama running (Settings → Local AI shows it) |
| **S4 · Local media** | §3 | ~30 min + waiting | Pick 3–5 videos without captions and 3–5 with slides; keep the laptop on power |
| **S5 · Cloud AI** | §5 | ~20 min | An API key (Claude or Gemini). Optional; skip if you only use local AI |
| **S6 · Setup and decisions** | §4 | ~15 min | Nothing |
| **S7 · Summaries and knowledge (Phase 3)** | S7 below | ~40 min + waiting | Ollama running; pick 4–5 videos of one category you know well |
| **S8 · Navigation, Settings and shortcuts** | S8 below | ~15 min | Nothing |
| **S9 · Failures, logs and always-on** | S9 below | ~15 min | Nothing |
| **S10 · Speak your interests** | S10 below | ~10 min | A microphone; local AI set up |
| **S11 · Home, Library ratings, Split** | S11 below | ~20 min | Local AI set up; a few rated videos |
| **S12 · Run anywhere** (one session per computer you have) | S12 below | ~20 min each | The computer to test; for S12.6–S12.8 Docker installed |

### S1 extra checks (new ratings, 2026-10-24)

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| S1.1 | Why this score | Open a rated video → "Why this score" | Four factors with reasons you agree with; the formula line adds up to the score | |
| S1.2 | Minutes | Read the Minutes of 3 videos you have watched | You could decide watch / skip from them alone; timestamps jump to the right part | |
| S1.3 | Off-interest video | Find a good video outside your interests | It still scores well (interest only subtracts up to 2) | |
| S1.4 | Your formula | Settings → Scoring: change the interest penalty, save, re-rate one video | The new number follows the formula; Reset to default works | |
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

### S8 checks (navigation, Settings and shortcuts, 2026-10-28)

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| S8.1 | Top bar | Visit each page from the top bar; then Search, Settings and Help icons | Six pages in the bar; the active one is marked; icons are easy to hit; nothing scrolls sideways on a phone-width window | |
| S8.2 | Ask pop-up | From Library, click the chat button → ask a question → a follow-up → click a cited moment → Open full page | Answer with chips; the moment plays; the full page continues the same conversation | |
| S8.3 | Ask about a video | Open a video → Ask about this video | The panel opens limited to that video; ✕ removes the limit | |
| S8.4 | Settings menu | Go through all seven sections in Settings | Each is clear on its own; Cloud AI and Local AI are separate, and AI by task can still mix them | |
| S8.5 | About you | Type 2 words in Interests → Save; then pick a language → Save | An error under the field and nothing saved; the language saves and new summaries use it | |
| S8.6 | Add category | Categories → Add category → name + keywords → Save; then Propose with AI | Inline row, no pop-up; the new category is protected, editable, and never offered for removal | |
| S8.7 | Keyboard only | Press `?`, then try every shortcut; Tab from the top of the page | All work, none fire while typing; "Skip to content" appears first; focus is always visible | |
| S8.8 | Old links | Open `#/ai` and `#/setup` | They land on Settings → Cloud AI and Help | |

### S9 checks (failures, logs and always-on, 2026-10-29)

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| S9.1 | Failed sync | Playlists → add a made-up link such as `https://www.youtube.com/playlist?list=PLzzzzzzzzzzzzzzzz` | A card with the cause, a fix, Try again, Dismiss, See details | |
| S9.2 | It sticks, then goes | Refresh the page; then Dismiss; refresh again | Still there after the first refresh; gone after Dismiss | |
| S9.3 | Retry works | Add a real playlist after a failed one, without restarting | It syncs (before the fix, everything failed until a restart) | |
| S9.4 | Remembered access | Sync your private playlist or Watch Later from its row | It uses the login it was added with; the row says "with your Chrome login" | |
| S9.5 | Logs | Help → Logs → Show recent problems; Download log | The failure is listed with cause and fix; no API key or `?t=` token in the file | |
| S9.6 | Always on | `bash scripts/service.sh install`; log out and in (or reboot) | The dashboard opens at http://127.0.0.1:8765 without starting anything | |
| S9.7 | Recovers | Imitate a crash: `pkill -9 -f dashboard.py`, wait 20 s | It is back (a normal stop with `systemctl --user stop yourtube` stays stopped) | |

### S10 checks (speak your interests, 2026-10-29)

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| S10.1 | Record | Settings → About you → 🎙 Record → talk 30–60 s about what you want to learn and skip → Stop | The browser asks for the mic once; the timer runs; the mic light goes off after Stop; your words appear in the box within ~15 s | |
| S10.2 | Your accent and terms | Say tool names and mixed Hindi/Gujarati/English if you use them | Text is close enough to fix by hand; note words it gets wrong | |
| S10.3 | Draft | Write my interests with AI | Four lines (Learn, Level, Skip, Goal); nothing invented; your existing interests that still apply are kept | |
| S10.4 | Use, not saved | Use this → leave the page without Save → come back | Interests are unchanged until you press Save | |
| S10.5 | Better ratings | Save the new interests, re-rate 5 videos (Library → Rate) | Scores and reasons match what you said better than before | |

### S11 checks (Home, Library ratings, Knowledge guide, Split, 2026-10-30)

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| S11.1 | Home | Open the dashboard; change minutes a day, days a week and speed | Lands on Home; the finish date changes; today's session, continue watching, top picks and "needs you" make sense | |
| S11.2 | Needs you | Click each link under Needs you | Each opens the right filtered view (suggestions, rating, playlists, knowledge, categories) | |
| S11.3 | Library ratings | Library → sort AI score → Watch / Skim / Skip chips → Skip the AI skips → Undo | Best first; chips filter with right counts; skip then undo restores the statuses | |
| S11.4 | Menu and old links | Use the five menu items; open `#/plan` and `#/triage`; press `G H`, `G ?` | Home and Library open; the shortcuts work | |
| S11.5 | Knowledge guide | Knowledge → Summarise 5 on a category you study → Build when it shows | The guide explains the steps; Build appears after 3 summaries | |
| S11.6 | Split | (Replaced by S15.3.) | | |
| S11.7 | Dropdowns | Look at every page in light and dark, desktop and phone | Same size dropdowns and fields, none inside headings, nothing misaligned | |

### S15 checks (simpler Categories page, 2026-10-31)

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| S15.1 | Fewer buttons | Open Categories | The top row has only Save and re-sort, Add category, Sort N unsorted videos with AI, and a Review link when suggestions wait | |
| S15.2 | Leftover proposal | Sort unsorted videos with AI when several fit no category | Suggestions for the ones that fit, and a box "New categories for N videos that fit none" to accept or dismiss | |
| S15.3 | Split into topics | On a category named like "A & B": Split into topics → confirm | A and B appear (existing ones reused); its videos get suggestions to review | |
| S15.4 | Empty library | Create a new library, add a playlist, open Categories | "Create categories from my interests"; it asks for About you first if that is empty | |

### S14 checks (local and cloud side by side, cloud media, 2026-10-31)

Needs at least one cloud key (Groq is cheapest for transcription). Note the costs your providers show afterwards.

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| S14.1 | Lanes | Start a local transcription, then a rating with a cloud AI, then a sync | All three run at once (task panel: YouTube, This computer, Cloud), each with its own log; Stop on one leaves the others running | |
| S14.2 | Cloud transcription | Settings → AI by task → Media → Transcription: Groq → transcribe 2 videos without captions | The cost is shown first; the transcript reads well and times line up when you click a line; the video panel names `groq:whisper-large-v3-turbo` | |
| S14.3 | Gemini / OpenAI transcription | Repeat S14.2 with Gemini, then OpenAI | Both work; Gemini's times are close but may drift a little | |
| S14.4 | Cloud slides | Media → Slide reading: Claude or Gemini → Read slides for 1 video | Cost shown first; slides and on-screen text appear like the local ones | |
| S14.5 | Helper AI | Ratings on the local model + Helper: a cloud AI → Rate 20 videos | Finishes faster than local alone; the log ends "N by …, M by …" | |
| S14.6 | Cloud index | Media → Meaning search index: Gemini → Save → confirm | The index rebuilds; Ask and By meaning search still find the right moments; switching back to This computer is instant | |
| S14.7 | Local only | Settings → Local AI → Local only on, then try S14.2 | Everything runs on this computer; no cloud call is made | |

### S13 checks (remove a playlist from YourTube, 2026-10-31)

Try it on a small playlist you can sync again, or on a copy of the library.

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| S13.1 | Counts | Add a note to one video of a small playlist → Playlists → **Remove** on it → read the dialog → Cancel | The dialog counts videos removed, kept for notes and shared with other playlists; Cancel changes nothing | |
| S13.2 | Remove | Remove again → OK | A notes .zip downloads; the playlist disappears; Library shows the noted video without a playlist, notes intact; other videos of that playlist are gone; nothing changed on YouTube | |
| S13.3 | Old answers | Open an old Ask answer that cited a removed video and click the citation | A message says the video was removed instead of an empty panel | |
| S13.4 | Add it back | Sync the same playlist again | Its videos come back (details, ratings and summaries are made again when you ask) | |

### S12 checks (run anywhere: restart, graphics cards, Docker, 2026-10-30)

Do the rows that match the computer in front of you, and note which computer (OS, graphics card) in the Result column.

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| S12.1 | Restart (Linux, macOS) | `bash scripts/service.sh install`, then `bash scripts/service.sh restart`, then `status` | Restarts without errors; the dashboard opens again with `open`. Before `install`, `restart` says the service isn't installed | |
| S12.2 | Restart (Windows) | `scripts\service.ps1 -Install`, then `-Restart`, then `-Status` | "YourTube dashboard restarted."; the dashboard opens with `-Open` | |
| S12.3 | Update | In a git checkout with the service installed: `bash scripts/service.sh update` | Pulls, refreshes dependencies, restarts; your library is unchanged | |
| S12.4 | Graphics card shown | Settings → Local AI → *Local AI on this computer* | The GPU line names your card (NVIDIA / Radeon / Apple M-chip / Intel) and the recommended model fits it. Intel and small laptop graphics say models run on the CPU | |
| S12.5 | Transcription on the GPU | `bash scripts/setup.sh --local-ai` (NVIDIA: CUDA libraries installed; Mac: mlx-whisper; AMD: follow "Graphics cards" in the guide), then transcribe one video without captions | The task's first line says "on the GPU" (on Intel and older laptops: "on the CPU"); the transcript looks right | |
| S12.6 | Docker start | `mkdir data && docker compose up -d --build`, then `docker compose exec yourtube python dashboard.py --print-url` and open the link | The dashboard opens; `data/` holds `yt_kb.db`, `settings.json` and `logs/` | |
| S12.7 | Docker playlists and AI | Add a public playlist; then a private one with `data/cookies.txt` (path `/data/cookies.txt`); rate a video with Ollama running on the host | Both sync; the rating uses the host's Ollama without typing an address (Linux: Ollama set to listen on Docker's bridge address, see Troubleshooting) | |
| S12.8 | Docker stays private | From another device on your network, open `http://<this computer's IP>:8765` | It doesn't connect | |

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
| 2.1 | Interests | Settings → About you → write what you want to learn and what you don't → Save | Saved | |
| 2.2 | Ratings spread | Library → Rate (on a mixed playlist, after Fetch details) | A mix of Watch / Skim / Skip, not everything 8–9 | |
| 2.3 | Skip verdicts | Read 5–10 Skip reasons, and open a couple of the videos | You agree they're skippable; note any you disagree with | |
| 2.4 | Skim ranges | Click a Skim range on a few videos | It lands on the part the reason describes | |
| 2.5 | Categories | Categories → Sort N unsorted videos with AI → review suggestions (and any new categories it proposes) | Sensible categories; suggestions mostly right; note the wrong ones | |
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
| 4.1 | Setup guide | Read the Help page (? icon) as if you were a new user | Clear; note anything missing or confusing | |
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
| 5.6 | Cost estimate | Library → Rate a few videos with the cloud AI | The estimate appears before it starts and is in line with what the provider's billing page shows afterwards | |
| 5.7 | Cloud quality | Rate the same 5 videos with local and cloud; ask the same 3 questions with both | Note which is better: score spread, Skip reasons, answer accuracy and citations | |
| 5.8 | Cloud categories | Categories → Propose with AI and Sort unsorted with the cloud AI | Sensible results; compare with local | |
| 5.9 | Local only | AI → tick Local only | Cloud AIs show as blocked; rating or asking with a cloud AI gives a clear message; nothing is sent | |

## 7. Libraries, queue and settings (feedback round, 2026-10-23) · session S2

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| 7.1 | Create a library | Playlists → Libraries → type "Health" → Create; move one or two playlists to it with the Library column | The header switcher shows Health; Home, Library, Search and Ask show only its videos | |
| 7.2 | Its own categories | In Health: set Health interests in Settings → About you, then Categories → Create categories from my interests → Apply | Health categories don't appear in My library; ratings in Health judge against Health interests | |
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

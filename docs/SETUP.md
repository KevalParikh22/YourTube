# YourTube setup guide

Everything you need to install, configure and run YourTube on your own computer. YourTube runs **locally**: your library, YouTube sign-in and API keys never leave your machine.

---

## 1. Requirements

| | Minimum | Notes |
|---|---|---|
| Operating system | Linux, macOS or Windows 10/11 | The setup script is tested on Linux. The Windows script is not yet tested. |
| Python | 3.10 or newer | `python3 --version` |
| Disk | About 300 MB for the app | Plus 2–7 GB per local AI model, if you use one |
| ffmpeg | Optional | Needed later for local transcription and slide reading (Phase 2.5) |
| GPU | Optional | A graphics card with 4 GB or more makes local AI fast: NVIDIA, AMD Radeon or Apple Silicon (see **Graphics cards** (section 5)). Without one, local models run on the CPU, more slowly. |
| Browser | Firefox, Chrome, Chromium, Brave or Edge, signed in to YouTube | Only needed for private playlists and Watch Later |

---

## 2. Quick start

### Choose how to run it

| | Native (recommended) | Docker (optional) |
|---|---|---|
| Linux | `scripts/setup.sh` | Works; GPU only for the AI in Ollama on the host |
| macOS | `scripts/setup.sh` | Works, but no Apple GPU inside Docker |
| Windows | `scripts\setup.ps1` (not yet tested) | Works with Docker Desktop |
| Private playlists and Watch Later | Reads your browser's sign-in | Needs a `cookies.txt` export (expires) |
| Local transcription | Yes (GPU on NVIDIA, AMD Radeon, Apple Silicon) | No (the image stays small) |

Pick **native** unless you don't want Python on your computer, or you only use cloud AI and public playlists. Docker is described in **Docker (optional)** (section 3).

Download the setup script from the dashboard (**Help** page, ? icon) or use the copy in `scripts/`, then run it **inside the YourTube folder** (the one containing `dashboard.py`).

**Linux and macOS**

```bash
bash scripts/setup.sh              # the app only
bash scripts/setup.sh --local-ai   # the app plus local AI (Ollama and a model that fits your computer)
bash scripts/setup.sh --local-ai --service   # recommended: also keep the dashboard running (see "Keep it running")
```

**Windows (PowerShell)**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1            # the app only
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -LocalAI   # the app plus local AI
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -LocalAI -Service   # also keep it running (not yet tested)
```

With `--service` the dashboard is now running and opens in your browser, and you can skip the next command. Otherwise start the dashboard. It opens in your browser through its **access link** (`http://127.0.0.1:8765/?t=…`, also printed in the terminal). The link carries a private token, so other people using the same computer can't open your dashboard. After the first visit your browser remembers it, and plain http://127.0.0.1:8765 works.

```bash
.venv/bin/python dashboard.py              # Windows: .venv\Scripts\python.exe dashboard.py
```

### Keep it running

Instead of starting it by hand each time, install it as a background service. It starts when you log in and restarts by itself if it crashes. If the YourTube folder is on a drive that mounts after login, the service keeps retrying every 15 seconds until the folder is there.

```bash
bash scripts/service.sh install      # Linux (systemd user service) and macOS (LaunchAgent)
bash scripts/service.sh open         # open the dashboard with its access link
bash scripts/service.sh status       # is it running?
bash scripts/service.sh uninstall    # stop it and remove the service
bash scripts/service.sh restart      # run the new code after an update
bash scripts/service.sh update       # git pull, refresh dependencies, then restart
```

Add `--port 8766` to `install` and `open` to use another port. On Windows: `powershell -ExecutionPolicy Bypass -File scripts\service.ps1 -Install` (also `-Open`, `-Status`, `-Restart`, `-Uninstall`; not yet tested). After updating YourTube, restart the service so it runs the new code: `bash scripts/service.sh restart` on Linux and macOS, `scripts\service.ps1 -Restart` on Windows. `update` needs a git checkout and the service installed (without the service it only updates).

Starting `dashboard.py` while it already runs prints "YourTube is already running on port 8765" and exits; open it with `bash scripts/service.sh open` or `.venv/bin/python dashboard.py --print-url`.

### What the setup script does

| Step | What happens | Asks first? |
|---|---|---|
| 1. Python | Checks for Python 3.10 or newer | — |
| 2. Environment | Creates `.venv` and installs `requirements.txt` (yt-dlp, anthropic, and secretstorage on Linux) | No (only inside the project folder) |
| 3. ffmpeg | Checks that it is installed and tells you how to install it if not | — |
| 4. Ollama *(local AI only)* | Installs Ollama with its official installer (Linux) or `winget` (Windows), then starts it | **Yes**. The installer may ask for your password |
| 4b. Local transcription *(local AI only)* | Installs faster-whisper from `requirements-local.txt` (about 400 MB; plus mlx-whisper on Apple Silicon). With an NVIDIA GPU also `requirements-nvidia.txt` (CUDA libraries, about 1 GB more). With an AMD GPU it points you to **Graphics cards** (section 5). The speech model downloads on first use (~1.6 GB) | **Yes** |
| 5. Model *(local AI only)* | Downloads the model that fits your GPU and makes it YourTube's default AI | **Yes** |
| 6. Embedding model *(local AI only)* | Downloads `qwen3-embedding:0.6b` (0.64 GB) for questions and search by meaning | **Yes** |
| 7. Service *(`--service` only)* | Installs the background service (see "Keep it running") and opens the dashboard | No (only files in your user folder) |
| Done | Prints the local AI check | — |

The script is safe to run again: every step skips what is already done.

### Script options

| Option | Linux/macOS | Windows | Effect |
|---|---|---|---|
| Local AI | `--local-ai` | `-LocalAI` | Also install Ollama and a model |
| No questions | `-y` / `--yes` | `-Yes` | Answer "yes" to every question |
| Keep it running | `--service` | `-Service` | Start the dashboard at login and restart it after a crash |
| Choose the model | `YTKB_MODEL=qwen3.5:2b bash scripts/setup.sh --local-ai` | `$env:YTKB_MODEL="qwen3.5:2b"` | Use this model instead of the recommendation |
| Help | `--help` | — | Show usage |

---

## 3. Manual setup (without the script)

```bash
# 1. Environment and dependencies
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
#    Debian/Ubuntu without python3-venv:
#    python3 -m venv --without-pip .venv && python3 -m pip --python .venv/bin/python install -r requirements.txt

# 2. Optional: local AI
curl -fsSL https://ollama.com/install.sh | sh     # macOS/Windows: https://ollama.com/download
ollama serve                                      # if it isn't already running as a service
.venv/bin/python local_setup.py                   # check this computer and see the recommended model
.venv/bin/python local_setup.py --pull qwen3.5:4b # download a model
.venv/bin/python local_setup.py --use qwen3.5:4b  # make it the default AI

# 3. Run
.venv/bin/python dashboard.py
```

### Docker (optional)

Install Docker (Docker Desktop on Windows and macOS), then inside the YourTube folder:

```bash
mkdir data                                  # your library, settings and logs live here
docker compose up -d --build                # build and start; it restarts by itself
docker compose exec yourtube python dashboard.py --print-url   # the access link to open
```

- **Your data** is in `./data` (database, settings, categories, logs, slide thumbnails). Back it up like `yt_kb.db`. Create the folder yourself before the first start. On Linux the container runs as user 1000; if your user ID is different, start it with `YTKB_UID=$(id -u) YTKB_GID=$(id -g) docker compose up -d`.
- **Only this computer can open it:** the port is published as `127.0.0.1:8765`. Don't change it to `8765:8765`: that puts the dashboard on your network, where the localhost check doesn't protect it.
- **Private playlists and Watch Later:** the container can't read your browser's sign-in. Export a `cookies.txt` for youtube.com with a browser extension, save it as `data/cookies.txt`, and choose *cookies.txt file* with the path **`/data/cookies.txt`** when adding the playlist. The export expires after a while; export it again when syncs fail. Public playlists need nothing.
- **Local AI:** run Ollama on the computer itself, not in Docker. The container reaches it at `host.docker.internal:11434` automatically (`YTKB_OLLAMA_URL`); an address you saved in Settings → Local AI wins. Local transcription is not in the image.
- **Update:** `git pull && docker compose up -d --build`. **Stop:** `docker compose down` (your data stays in `./data`).

---

## 4. Adding playlists and signing in to YouTube

YourTube **only reads** your playlists. It never adds, removes or reorders anything on YouTube.

| Playlist type | Sign-in needed | How |
|---|---|---|
| Public or unlisted | No | Paste the link or ID |
| Private, or Watch Later (`WL`) | Yes | Use your browser's YouTube sign-in |

In the dashboard: **Playlists → Add or sync a playlist**, then choose **Detect my browser login**.

On the command line:

```bash
.venv/bin/python main.py --list                       # your playlists and their IDs (uses your browser sign-in)
.venv/bin/python main.py WL --browser auto            # Watch Later
.venv/bin/python main.py PLxxxxxxxx --browser auto    # any playlist by ID
.venv/bin/python main.py PLxxxxxxxx --browser firefox # a specific browser
.venv/bin/python main.py PLxxxxxxxx --cookies cookies.txt
```

**Notes by system**

- **Linux:** Chrome cookies need the `secretstorage` package (in requirements.txt) and an unlocked keyring; Firefox works without.
- **Windows:** Chrome, Edge and Brave use app-bound encryption, so their cookies usually can't be read; use Firefox or a cookies.txt export. The setup script is `scripts\setup.ps1` (not yet tested on Windows).
- **macOS:** reading Chrome cookies triggers a Keychain prompt; allow it, or use Firefox.
- **Several Google accounts:** "Detect" tries each browser signed in to YouTube until one can read the playlist (one that returns it empty is used only if none returns videos). If the playlist belongs to an account not signed in to any browser, sign in with that account, or export a `cookies.txt` while it is active. Listing your playlists (`--list`) still uses the first signed-in browser. On the Playlists page, each playlist's own **Sync** button syncs that playlist again with the sign-in it last used; to add another playlist, paste its link in the box and use the main **Sync** button.
- **Keep `cookies.txt` private.** It works like your password.

---

## 5. AI setup

AI is optional. Without it, categories come from keywords. With it you get proposed categories, per-video category suggestions, and **watch ratings** (Watch / Skim / Skip with a reason and a summary). The AI only suggests: nothing changes until you accept it.

### Local AI (free, private, offline)

Set it up with `bash scripts/setup.sh --local-ai`, or on **Settings → Local AI** → *Local AI on this computer* → **Download** → **Use**.

| Your GPU memory | Recommended model | Download | Notes |
|---|---|---|---|
| 8 GB or more | `qwen3.5:9b` | 6.6 GB | Best quality that fits |
| 4 GB | `qwen3.5:4b` | 3.3 GB | Default recommendation; reads text and images |
| Less than 4 GB, or CPU only | `qwen3.5:2b` | 2.7 GB | Faster, less accurate |

Any Ollama model can be downloaded by name in Settings → Local AI (for example `gemma4:e2b`).

- **Context size** (Settings → Local AI → Ollama → Edit): how much transcript the model reads at once. The default is 8192 tokens. Larger values use more memory.
- **Local only** (Settings → Local AI): blocks every cloud AI, so nothing is sent off your computer.

### Questions and search by meaning

**Ask** and **Search → By meaning** match pieces by what they mean, in any language, using a small local embedding model. Without it they still work with keywords only.

Settings → Local AI → *Local AI on this computer* → **Meaning-based search**:
- **Download** the embedding model (`qwen3-embedding:0.6b`, 0.64 GB, recommended; `qwen3-embedding:4b`, 2.5 GB, matches better but indexes about 5× slower). The index is built right after the download.
- The status line shows how many pieces are indexed. The index updates by itself after syncs, details, transcription, slide reading and ratings; **Update index** does it now.
- **Rebuild** embeds everything again (for example after an Ollama or model update). **Use** switches to another downloaded model and re-indexes the library with it; vectors from the old model are dropped.

Command line: `ollama pull qwen3-embedding:0.6b`, then `.venv/bin/python main.py --embed`. Choose another model with `.venv/bin/python local_setup.py --use-embed MODEL`.

### Local transcription (videos without usable captions)

For videos with **no captions**, or captions in the **wrong script** (for example English speech that YouTube auto-captioned in Devanagari), YourTube can transcribe the audio on your computer with Whisper.

- **Run it:** Playlists → *Video details and transcripts* → **Transcribe N videos locally** (shows the audio length and an estimate), or **Transcribe locally** in a video's panel.
- **What happens:** only the original-language audio is downloaded, anonymously; the AI model is unloaded from the GPU; Whisper transcribes; and the audio is deleted.
- **Speed:** on an RTX 3050 (4 GB) about 2.5 min per hour of audio on the GPU (`large-v3-turbo`); on the CPU (`small`) about 10–16 min per hour (16 min measured on a Ryzen 7 7730U laptop). Choose the model in Settings → Local AI (*Speech-to-text model*; Auto picks by device).
- **Both kept:** when a video already has captions, both transcripts are stored. The video panel's *Transcript used for search, ratings and summaries* switch picks which one counts. After switching, use **Rate again** to refresh that video's rating.
- **Needs:** `faster-whisper` (installed by `scripts/setup.sh --local-ai`) and ffmpeg. Which graphics cards it uses: see **Graphics cards** (section 5).

### Graphics cards

| Card | Local AI (Ollama) | Local transcription | Slide reading |
|---|---|---|---|
| **NVIDIA** | GPU | GPU (faster-whisper, CUDA; `requirements-nvidia.txt`) | GPU (through Ollama) |
| **AMD Radeon** RX 6000 / 7000 / 9000 | GPU (ROCm; on Windows RX 7000 only, others through Vulkan) | GPU after a manual step (below); otherwise CPU | GPU (through Ollama) |
| **Apple Silicon** (M1 and later) | GPU (Metal) | GPU (mlx-whisper, installed automatically) | GPU (through Ollama) |
| **Intel** Arc / integrated | Usually CPU (Ollama's Intel support is unofficial, through Vulkan) | CPU | Follows Ollama |
| Older AMD laptop graphics (e.g. Vega in Ryzen 5000) | CPU | CPU | CPU |
| No graphics card | CPU | CPU | CPU |

Settings → Local AI → *Local AI on this computer* shows the card YourTube found and sizes the recommended model to it. Apple Silicon shares memory with the system, so about 65 % of it counts as graphics memory.

**AMD: transcription on the GPU (manual, not yet tested).** faster-whisper runs on AMD cards through CTranslate2's ROCm build:
1. Install AMD's ROCm 7.2 runtime (Linux: AMD's `amdgpu-install`; Windows: the ROCm 7.2 wheels from AMD, since the 7.1.1 HIP SDK installer doesn't work with it).
2. Download `rocm-python-wheels-Linux.zip` or `rocm-python-wheels-Windows.zip` from the latest release at https://github.com/OpenNMT/CTranslate2/releases, unzip it, and install the wheel for your Python version: `.venv/bin/python -m pip install --force-reinstall --no-deps ctranslate2-*.whl`.
3. Restart YourTube. The next transcription job's first line says it runs on the GPU.

### Slides, whiteboards and on-screen code

Many tutorials put the real content on screen. YourTube can read it with the local vision model (the Ollama model, for example `qwen3.5:4b`, which reads images).

- **Choose videos:** tick **Has slides / whiteboard / on-screen code** in a video's panel, switch on **Slides** for a category (Categories page), or click **Mark N suggested videos** (Playlists page). Suggestions come from titles ("lecture", "explained", "system design"…) and from transcripts where the speaker refers to the screen.
- **Run it:** Playlists → *Slides and whiteboards* → **Read slides for N videos**, or **Read slides now** in a video's panel.
- **What happens:** only the video picture is downloaded (no audio), anonymously, at 480p (720p for small text: Settings → Local AI → *Slide frame quality*). Frames are picked at scene changes and at least every 30 seconds, near-duplicates are dropped, and the vision model reads each frame, skipping frames of a person talking. The video is deleted; small thumbnails (a few KB each) are kept in `frames/`.
- **What you get:** a clickable slide strip and the on-screen text in the video panel; a **Slides** filter in search; slide text in ratings and in exported notes.
- **Speed (RTX 3050):** about 1.5–2 min per 10 min of video, plus the download.
- **Vision model:** Settings → Local AI → *Vision model (slides)*. Only models that can read images can be picked.

### Cloud AI

On **Settings → Cloud AI**, click **Set up** next to a provider, paste the key, then **Save and test**. You can also set the key as an environment variable instead of saving it.

| Provider | Key from | Environment variable | Example models |
|---|---|---|---|
| Claude (Anthropic) | console.anthropic.com | `ANTHROPIC_API_KEY` | `claude-haiku-5-5` (cheapest), `claude-sonnet-5-5`, `claude-opus-5-5` |
| Gemini (Google) | aistudio.google.com | `GEMINI_API_KEY` | `gemini-2.5-flash`, `gemini-2.5-flash-lite`, `gemini-2.5-pro` |
| Groq | console.groq.com | `GROQ_API_KEY` | `llama-3.3-70b-versatile`; also cloud transcription (`whisper-large-v3-turbo`, about $0.04 per audio hour) |
| OpenAI | platform.openai.com | `OPENAI_API_KEY` | `gpt-5.4-mini`; also cloud transcription (`whisper-1`, about $0.36 per audio hour, shuts down 26 Feb 2027) and the meaning index (`text-embedding-3-small`) |
| Open-weight (OpenAI-compatible: OpenRouter, Together…) | the host's site | `OPENAI_COMPAT_API_KEY` | depends on the host; set the Server URL too |
| LM Studio (local) | — | — | load a model and start its local server |

### Which AI does what

In **Settings → AI by task** (*Which AI does what*), pick a **Default** AI, then optionally a different one for **Categories**, **Ratings**, **Summaries**, **Questions and answers** and **About you** (drafting your interests). Example: a free local model for categories and Claude for ratings. A rating run shows the estimated tokens and cost before it starts. **Summaries**, **Questions and answers** and **About you** use the same AI as ratings unless you pick one.

**Media** (same page): where the heavy steps run. Each is **This computer** unless you choose a cloud service with a key:

| Step | Cloud choices | What is sent | Rough cost |
|---|---|---|---|
| Transcription | Groq, OpenAI, Gemini | the video's audio (deleted here afterwards) | Groq $0.04, Gemini ≈ $0.09, OpenAI $0.36 per audio hour. Gemini's timestamps are approximate |
| Slide reading | Claude, Gemini, OpenAI, OpenAI-compatible | the slide frames (about 40 for a 30-minute talk) | well under $0.01 per video |
| Meaning search index | Gemini, OpenAI | all indexed text (titles, transcripts, notes…) | about $0.10 (Gemini) or $0.02 (OpenAI) for 9,000 pieces |

Switching the index to another service builds a new index; the old one is kept, so switching back is instant. Every cloud run shows its estimated cost first and asks before it starts. **Local only** (Settings → Local AI) blocks all of them.

**Helper AI (works at the same time):** for Ratings, Summaries, Transcription and Slide reading you can add a helper: a cloud service when the main AI is local, or this computer when the main AI is cloud. A big batch is shared: each takes the next video when it is free, so the batch finishes sooner, and the task log says which AI did each video. The cost shown before a run is the most the cloud part can cost.

### Settings

Settings has a side menu: **General** (default playback speed and theme, kept in this browser), **About you**, **AI by task**, **Cloud AI**, **Local AI**, **Sync and details** and **Scoring** (how scores are calculated).

**About you:**
- **What are you looking for?** Say it (🎙 **Record**, up to 3 minutes; you can record several times) or type it, in your own words. **Write my interests with AI** turns it into four short lines (Learn, Level, Skip, Goal), keeping what still applies from your current interests. **Use this** puts the draft into Interests; nothing is saved until you press **Save**. Voice is turned into text on your computer by Whisper (`small` model on the CPU, about 10 seconds per minute of speech; the first use downloads it, ~0.5 GB) and the recording is deleted right away. It needs local transcription (`bash scripts/setup.sh --local-ai`); the browser asks once for microphone access.
- **Interests:** what you want to learn. Ratings favour videos that match. Optional; if filled in, at least 3 words and at most 2,000 characters.
- **Language:** summaries and rating reasons are written in this language (default English), chosen from the list of supported languages.

**Sync and details:** fetch details for new videos after each sync, use descriptions in search and AI, and suggest categories for new videos after each sync.

### Getting around

The top bar holds the main pages and, on the right, Search, Settings and Help:

- **Home**: time left and your finish date (set minutes a day, days a week and speed), today's session, what you're watching, the AI's top picks, and what needs you (suggestions to review, videos to rate, a failed sync, out-of-date knowledge pages, unsorted videos). Opening the dashboard lands here.
- **Library**: every video, with filters, search and actions. Sort by **AI score** and filter by **Watch / Skim / Skip / Not rated**; **Rate N unrated** and **Skip the N AI skips** (with Undo) appear above the list.
- **Knowledge**: topic pages built from summaries. The first time, it shows how: per category **Summarise 5**, then **Build** once 3 videos are summarised.
- **Playlists** and **Categories**.

Old links to Plan and Triage open Home and the Library (sorted by AI score). **Ask** is the round chat button at the bottom right: it opens a small panel, and **Open full page** continues the same conversation on the Ask page. **Help** shows this guide, the downloads and the keyboard shortcuts.

| Key | Action |
|---|---|
| `/` | Search |
| `A` | Ask your library |
| `?` | Show keyboard shortcuts |
| `G` then `H` `L` `K` `Y` `C` `S` | Home, Library, Knowledge, Playlists, Categories, Settings |
| `G` then `?` | Help |
| `Esc` | Close the dialog, Ask panel or video panel |
| `J` / `K` | Next / previous video in the video panel |

Shortcuts are ignored while you type in a field.

---

## 6. Everyday use

| Task | Dashboard | Command line |
|---|---|---|
| Sync a playlist | Playlists → Sync | `.venv/bin/python main.py <ID> --browser auto` |
| Remove a playlist from YourTube | Playlists → **Remove** on its row. The dialog counts what happens first. Videos only in that playlist are removed with their subtitles, transcripts, ratings, summaries and slides; ones with your notes keep their notes, title and status (and the notes are downloaded as a .zip first); videos also in other playlists stay. Nothing changes on YouTube. Sync it again later to add it back (its fetched data starts over) | — |
| Add one video | Playlists → paste the video link → Sync. It goes to the source *Added one by one*, which only grows; its details are fetched right away | — |
| Separate libraries (e.g. Tech, Health, Personal) | Playlists → **Libraries** → Create; move playlists with the Library column; switch at the top of the page. Each library has its own categories, interests, time left, ratings and answers | — |
| See or manage running tasks | The status button in the header: what runs, what waits (Remove), recent results. Tasks run in three lanes at the same time: **YouTube** (syncs, details), **This computer** (local AI, transcription, slides, index) and **Cloud** (cloud AI). Each lane runs one task at a time and queues the rest; each running task has its own log and Stop | — |
| Fetch descriptions, chapters and captions | Playlists → Video details → Fetch | `.venv/bin/python main.py --details 200` |
| Rate videos (Watch / Skim / Skip) | Library → **Rate N unrated** (or Home → Needs you), then sort by AI score | — |
| Read slides, whiteboards and on-screen code | Mark videos (panel, category **Slides** switch, or suggestions), then Playlists → Read slides | — |
| Transcribe videos without usable captions on this computer | Playlists → Transcribe N videos locally, or a video's panel | — |
| Search titles, chapters, transcripts, summaries and notes | Search box in the header (or press `/`), then click a hit to jump to that moment. Tick **By meaning** to also find the same idea in other words or another language | — |
| Ask a question across your videos | **Ask** → type the question → click a number in the answer to play that moment. Ask follow-ups below the answer ("and where does he show the code?"); **+ New question** starts over. Past conversations stay in the list on the right; **Export note** saves one as Markdown | — |
| Summarise a video | Open it → **Summarise** (or select videos → Summarise selected). About 1–2 minutes per video with a local model. You get a TL;DR, minutes with timestamps, key claims and quotes, check-yourself questions, takeaways, related moments in your other videos, and Learn more (prerequisites, deeper, other views, YouTube searches) | — |
| Make a category more specific | Categories → **Split with AI** on a category with 6+ videos → tick the proposed categories → **Create and sort**. The new categories are protected; the old category's videos (also ones you placed by hand) get suggestions you review in Library → AI suggestions | — |
| Knowledge pages | **Knowledge** tab → **Summarise 5** for a category, then **Build** (or Update) once 3 are summarised → Open; every point links to the moment it comes from; **Export note** for Obsidian | — |
| Ask about one video | Open the video → **Ask about this video** | — |
| Category suggestions after sync | Settings → Sync and details → checkbox | `main.py <ID> --browser auto --ai` |
| Check local AI | AI → Local AI on this computer | `.venv/bin/python local_setup.py` |
| Export notes for Obsidian / note apps (Markdown) | Library → Export → Notes (.zip), the selection bar's **Export notes**, or **Export note** in a video's panel | — |
| Export your library | Library → Export → CSV or JSON | `curl -o library.csv "http://127.0.0.1:8765/api/export?format=csv"` (dashboard running) |

---

## 7. Configuration reference

### Files (all in the YourTube folder, none committed to git)

With `YTKB_DATA_DIR` set (Docker sets it to `/data`), all of these except `.venv/` live in that folder instead.

| File | Contents | Safe to delete? |
|---|---|---|
| `yt_kb.db` | Your library: playlists, videos, statuses, notes, transcripts, ratings, questions and answers | **No**. This is your data; back it up |
| `settings.json` | AI settings and API keys, interests, language, transcript switches | Yes (you lose AI settings and keys) |
| `taxonomy.json` | Your categories and their keywords | Yes (built-in categories come back) |
| `reports/` | Old static reports from earlier versions (no longer written) | Yes |
| `frames/` | Slide thumbnails for the slide strip | Yes (the strip disappears; read slides again to restore it) |
| `media_cache/` | Temporary audio and video while processing; emptied after each job | Yes |
| `logs/` | `yourtube.log` (and up to 5 older copies of 2 MB each): failures with their cause, fix and details. Keys, cookies and the access token are blanked out | Yes |
| `.venv/` | The Python environment | Yes (run the setup again) |

### `settings.json` keys

Normally edited through the AI, Categories and Playlists pages. Edit it by hand only while the dashboard is stopped.

| Key | Default | Meaning |
|---|---|---|
| `provider` | `"none"` | Default AI: `anthropic`, `gemini`, `openai_compat`, `ollama`, `lmstudio` or `none` |
| `providers.<name>.model` | per provider | Model name |
| `providers.<name>.base_url` | per provider | Server URL (Ollama: `http://localhost:11434`) |
| `providers.<name>.api_key` | `""` | API key (or use the environment variable) |
| `providers.ollama.num_ctx` | `8192` | Ollama context size in tokens |
| `tasks.categorize`, `tasks.rate`, `tasks.summarize`, `tasks.ask` | unset | Provider for that job; unset means the default (`summarize` and `ask` follow `rate`) |
| `dashboard_token` | created on first start | The dashboard's access token. Delete it to make a new one (old links stop working) |
| `show_descriptions` | `false` | Show video descriptions open in the video panel (they are collapsed by default) |
| `local_only` | `false` | Block every cloud AI |
| `visuals.<category>` | off | Read slides for videos in this category |
| `vision_model` | `""` | Ollama model that reads frames; empty means the Ollama text model |
| `visual_height` | `480` | Frame quality for slide reading: `480` or `720` |
| `rating_formula` | `{}` | Overrides for how scores are computed: `depth`, `value`, `freshness` (weights 0–1), `interest_penalty` (0–5, default 2), `interest_bonus` (0–2, default 0.5) |
| `use_descriptions` | `true` | Search video descriptions and send them to the AI (they are fetched with the captions either way) |
| `embed_model` | `"qwen3-embedding:0.6b"` | Ollama model for meaning-based search and questions (Phase 2) |
| `asr_model` | `"auto"` | Local speech-to-text model: `auto` (large-v3-turbo on a GPU, small on the CPU), `large-v3-turbo`, `small` or `medium`. On Apple Silicon `auto` uses `mlx-community/whisper-large-v3-turbo`; another `mlx-community/...` name is used as given |
| `interests` | `""` | What you want to learn (used by ratings) |
| `language` | `"English"` | Language for summaries and reasons |
| `auto_after_sync` | `false` | Suggest categories after each sync |
| `details_after_sync` | `false` | Fetch video details for new videos after each sync (up to 50) |
| `transcripts.<category>` | music-like names off | Whether to fetch captions for a category |
| `presets` | `[]` | Categories the AI must keep exactly ("Keep") |

### Environment variables

| Variable | Effect |
|---|---|
| `YTKB_DB` | Use another database file (handy for testing) |
| `YTKB_TAXONOMY` | Use another categories file |
| `YTKB_SETTINGS` | Use another settings file |
| `YTKB_LOG_DIR` | Write `yourtube.log` to another folder |
| `YTKB_DATA_DIR` | Keep the database, settings, categories, logs, slide thumbnails and temporary media in this folder (Docker: `/data`). The variables above still win |
| `YTKB_OLLAMA_URL` | Ollama address to use while the saved one is the default `http://localhost:11434` (Docker: `http://host.docker.internal:11434`) |
| `YTKB_UID`, `YTKB_GID` | Docker only: the user the container runs as (default 1000), so it can write to `./data` |
| `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENAI_API_KEY`, `OPENAI_COMPAT_API_KEY` | API keys, used when no key is saved in Settings → Cloud AI (`OPENAI_API_KEY` is now for OpenAI itself; OpenRouter and other OpenAI-compatible hosts use `OPENAI_COMPAT_API_KEY`) |
| `YTKB_MODEL` | Model the setup script downloads instead of the recommendation |

### Command-line reference

```text
dashboard.py   [--port 8765] [--host 127.0.0.1] [--no-open] [--print-url]   # --print-url: show the access link and exit
               # --host 0.0.0.0 only inside Docker (the published port stays on 127.0.0.1)
main.py        [URL|ID|WL] [--browser auto|firefox|chrome|chromium|brave|edge] [--cookies FILE]
               [--ai] [--list] [--details [N]]
local_setup.py [--pull MODEL] [--use MODEL] [--recommended] [--embed-model] [--use-embed MODEL] [--gpu-vendor]
scripts/service.sh {install|uninstall|status|open|restart|update} [--port N]
main.py        --embed          # update the meaning-based search index
```

---

## 8. Updating

```bash
bash scripts/service.sh update             # git pull, refresh dependencies, restart the service
# or by hand:
git pull                                   # if you got YourTube with git
bash scripts/setup.sh                      # refresh dependencies
bash scripts/service.sh restart            # if you use the background service (Windows: scripts\service.ps1 -Restart)
.venv/bin/python -m pip install -U yt-dlp  # when syncs suddenly fail: YouTube changed something
# venv made --without-pip ("No module named pip"): python3 -m pip --python .venv/bin/python install -U yt-dlp
```

Your database is upgraded automatically when the dashboard starts. Back up `yt_kb.db` before big updates.

---

## 9. Troubleshooting

When something fails, the task's card shows the cause and a fix. **Try again** re-runs it; **Dismiss** hides it. Every failure is also written to `logs/yourtube.log` with the details; the Help page (? icon) shows recent problems and can download the log to attach to a bug report.

| Problem | Fix |
|---|---|
| A sync fails, and trying again fails too | Read the cause on the card, or in Help → Logs → Show recent problems. A playlist re-syncs with the access it was added with; for a private playlist or Watch Later choose your browser login before adding it. |
| The dashboard doesn't open after a restart | `bash scripts/service.sh status`; if it isn't installed, start it with `.venv/bin/python dashboard.py` or install the service. |
| "The playlist does not exist" | Wrong ID, or a private playlist read without a sign-in. Run `main.py --list`. |
| "YouTube rotated the … sign-in during the read" (yt-dlp: "cookies are no longer valid") | YouTube replaced the browser's sign-in while YourTube was reading it, so the private playlist looked missing. YourTube already retries once. Open youtube.com in that browser, reload the page, wait a minute, then sync again; or use a `cookies.txt` exported from a private window. |
| Private playlist has 0 videos | The chosen browser isn't signed in to YouTube. Try **Detect**, or another browser. |
| "Confirm you're not a bot" during Fetch details | YouTube is rate-limiting. Wait an hour or two; progress is kept. |
| Ollama "not running" | Run `ollama serve` (or `systemctl start ollama`). |
| Local model very slow | The model doesn't fit in GPU memory. Pick a smaller one, or lower the context size. |
| AI returns errors | Settings → Cloud AI or Local AI → **Save and test** shows the exact error. |
| `pip install` says "externally-managed-environment" | Don't install into the system Python. Run `bash scripts/setup.sh`, or `python3 -m pip --python .venv/bin/python install -r requirements.txt` |
| Local transcription "needs faster-whisper" | `bash scripts/setup.sh --local-ai`, or `.venv/bin/python -m pip install -r requirements-local.txt` |
| Local transcription very slow | It runs on the CPU: check the GPU line in Settings → Local AI. NVIDIA: `.venv/bin/python -m pip install -r requirements-nvidia.txt`. AMD: see **Graphics cards** (section 5). Intel GPUs and older laptop graphics always use the CPU. |
| Docker: the dashboard can't reach Ollama | Ollama must run on the computer itself. Docker Desktop (Windows, macOS) reaches it as is. On Linux Ollama listens only on 127.0.0.1, which containers can't reach: set `OLLAMA_HOST=172.17.0.1` (Docker's bridge address, see `ip addr show docker0`) in Ollama's service with `sudo systemctl edit ollama`. Avoid `0.0.0.0` unless a firewall blocks port 11434, since that opens Ollama to your network. |
| Docker: "Permission denied" under `/data` | Create `./data` before the first start, and start with `YTKB_UID=$(id -u) YTKB_GID=$(id -g) docker compose up -d`. |
| Answers say "keywords only" | The meaning index is off: download the embedding model (`ollama pull qwen3-embedding:0.6b`), then run `.venv/bin/python main.py --embed`. |
| An answer misses something you know is in a video | Fetch details (or transcribe) for that video first. Try other words: the small local model links meanings well but not always (it misses "offline" for "running locally"). |
| The dashboard says "Open your dashboard link" | Open the link printed when it started, or get it with `.venv/bin/python dashboard.py --print-url`. Each browser needs the link once. |
| Dashboard won't start: port in use | `.venv/bin/python dashboard.py --port 8770` |
| Windows script blocked | Run it with `powershell -ExecutionPolicy Bypass -File scripts\setup.ps1` |

---

## 10. Privacy and safety

- **YouTube:** read-only. Playlists are read with your browser sign-in, and per-video details are read **without** it.
- **Cookies:** read locally and sent only to youtube.com; never stored or logged by YourTube.
- **AI:** only titles, descriptions and transcripts are sent, and only to the AI you choose. A question sends the question and about 8 matching excerpts. With **Local only**, nothing leaves your computer.
- **Dashboard:** listens only on 127.0.0.1 (in Docker the port is published on 127.0.0.1 only) and refuses requests from other websites. It has no password, so on a shared computer other local users could open it.
- **API keys:** stored in `settings.json`, which is created readable only by you. On NTFS/FAT drives that protection doesn't apply, so prefer environment variables there.

## 11. Uninstall

Delete the YourTube folder. If you installed Ollama only for YourTube, remove its models with `ollama rm <model>` and uninstall it as described at https://ollama.com.

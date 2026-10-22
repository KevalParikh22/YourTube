# ⚡ YourTube (Phase 0)

A non-destructive ingestion, classification, and watch-time optimization engine for your private and unlisted YouTube playlists.

---

## 🌟 What Phase 0 Delivers
1. **100% Non-Destructive Ingestion**: Never deletes or modifies your source playlist. Zero video files downloaded.
2. **Zero-Quota Ingestion**: Uses open-source `yt-dlp` to read private or unlisted playlist metadata and durations without consuming Google Cloud API quotas.
3. **Automated Categorization**: Classifies videos into clean thematic categories (AI, System Design, Coding, DevOps, Productivity, etc.).
4. **2.0x Watch-Time Engine**: Computes exact video counts, standard length (1.0x), accelerated duration (**2.0x / 1.5x**), and total hours saved.
5. **Instant Categorized Playlists**: Generates 1-click YouTube Queue URLs to play entire categories back-to-back without needing API write permissions.
6. **Local Persistence & Visual Reports**: Stores state in SQLite (`yt_kb.db`) and generates both an interactive HTML dashboard and a Markdown knowledge base.

---

## 🚀 Quick Start

> **Full setup guide:** [`docs/SETUP.md`](docs/SETUP.md) covers every command, option and setting. It is also shown on the dashboard's **Setup** page, which has download links for the setup scripts.
>
> One-step setup: `bash scripts/setup.sh --local-ai` (Linux/macOS) or `scripts\setup.ps1 -LocalAI` (Windows, untested).

### 0. Install
```bash
python3 -m venv .venv            # on Debian/Ubuntu without python3-venv: python3 -m venv --without-pip .venv
python3 -m pip --python .venv/bin/python install -r requirements.txt
```
Then run everything with `.venv/bin/python` (e.g. `.venv/bin/python dashboard.py`).

### 1. Run Interactively
Simply run:
```bash
python main.py
```
It will guide you to enter your playlist URL and select whether to read private session cookies from your browser (Chrome, Edge, Brave, Firefox) or treat it as unlisted.

### 2. Run with Command-Line Arguments

#### If your playlist is **Unlisted** or Public:
```bash
python main.py "https://www.youtube.com/playlist?list=YOUR_PLAYLIST_ID"
```

#### If your playlist is **Private** or Watch Later (uses your browser's YouTube sign-in):
```bash
python main.py "https://www.youtube.com/playlist?list=YOUR_PLAYLIST_ID" --browser auto
python main.py WL --browser auto --ai      # Watch Later, then ask the AI for category suggestions
```
Not sure of the ID? `python main.py --list --browser auto` lists your playlists with their IDs.

*`auto` uses the first browser signed in to YouTube. You can also name one: `firefox`, `chrome`, `brave`, `edge`. On Linux, Chrome needs the `secretstorage` package (in requirements.txt).*

#### Watch Later or private playlists on Windows
Chrome/Edge cookies are protected by app-bound encryption on Windows and often can't be read. Prefer Firefox, or export a `cookies.txt`:
```bash
python main.py WL --browser firefox
python main.py WL --cookies "C:\path\to\cookies.txt"
```
Keep `cookies.txt` private. It grants access to your YouTube session.

### 3. Local Dashboard (recommended)
```bash
python dashboard.py            # opens http://127.0.0.1:8765/
```
- **Overview**: totals, remaining time at your chosen speed, and per-category progress.
- **Library**: search and filter, change status or category inline or in bulk, play queues, CSV/JSON export.
- **Planner**: finish dates for a daily time budget, and a session builder.
- **Categories**: edit keywords and re-run auto-classification. Manual choices are kept.
- **Playlists**: run or re-sync playlists from the browser ("Detect my browser login" for private ones).
- **AI**: choose Claude, Gemini, an open-weight model (any OpenAI-compatible API such as OpenRouter or Groq) or a local model (Ollama, LM Studio).

Re-syncing keeps your watch statuses, notes and manually set categories. Videos removed from the source are flagged, never deleted.

### 4. AI categories (optional)
1. On the **AI** page, pick a provider and model, add an API key if it needs one, then click **Save and test**.
2. On **Categories**, tick **Keep** on the categories you always want, then click **Propose categories with AI**. Review the proposal, edit it, and apply.
3. Click **Sort unsorted with AI**, then review the suggestions under **Library → AI suggestions**. Accept them one by one, all of them, or only those at 80%+ confidence.

The AI only suggests. Nothing changes until you accept. Only titles and channel names are sent. API keys stay in `settings.json` on your computer (git-ignored).

### Tests
```bash
.venv/bin/python -m unittest
```

See [`docs/Phase-0-Research-and-Improvements.md`](docs/Phase-0-Research-and-Improvements.md) for the research review and the roadmap.

---

## 📂 Outputs & Reports
- **Interactive Dashboard**: [`reports/playlist_dashboard.html`](reports/playlist_dashboard.html) (Opens in your browser with stat cards, 2x duration metrics, and 1-click "Play on YouTube" queue buttons).
- **Markdown Knowledge Base**: [`reports/playlist_summary.md`](reports/playlist_summary.md) (Structured table of categories, duration breakdowns, and video links).
- **Database**: `yt_kb.db` (Local SQLite database tracking all video metadata and watch statuses).

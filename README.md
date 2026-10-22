# YourTube

A local-first assistant for your YouTube backlog. It reads your playlists (including private ones and Watch Later, through your browser sign-in), and never changes anything on YouTube. Then it:

- sorts videos into categories (keywords, or AI that you review);
- works out how long your backlog takes at 1× to 2× speed, and plans it;
- fetches captions, chapters and descriptions, or transcribes videos locally;
- rates each video Watch / Skim / Skip with a reason, summary and the parts worth watching;
- searches everything (by words or by meaning) and answers questions from your videos, citing the exact moments.

Everything runs on your computer. AI can be local (Ollama) or a cloud provider you choose, per task.

```bash
bash scripts/setup.sh --local-ai     # Windows: scripts\setup.ps1 -LocalAI
.venv/bin/python dashboard.py        # opens http://127.0.0.1:8765
```

- Setup and configuration: [docs/SETUP.md](docs/SETUP.md) (also in the app: Settings → Setup and help)
- Checks that need a person: [docs/MANUAL-TESTS.md](docs/MANUAL-TESTS.md)
- Plans: `docs/PRD-*.md`

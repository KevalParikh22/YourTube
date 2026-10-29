# YourTube

A local-first assistant for your YouTube backlog. It reads your playlists (including private ones and Watch Later, through your browser sign-in), and never changes anything on YouTube. Then it:

- sorts videos into categories (keywords, or AI that you review);
- works out how long your backlog takes at 1× to 2× speed, and plans it;
- fetches captions, chapters and descriptions, or transcribes videos locally;
- rates each video Watch / Skim / Skip with a reason, summary and the parts worth watching;
- searches everything (by words or by meaning) and answers questions from your videos, citing the exact moments.

Everything runs on your computer. AI can be local (Ollama) or a cloud provider you choose, per task.

```bash
bash scripts/setup.sh --local-ai --service   # Windows: scripts\setup.ps1 -LocalAI -Service
```

That sets everything up, starts the dashboard in the background (and again at every login), and opens it at http://127.0.0.1:8765. Without `--service`, start it yourself with `.venv/bin/python dashboard.py`. Undo with `bash scripts/service.sh uninstall`.

- Setup and configuration: [docs/SETUP.md](docs/SETUP.md) (also in the app: the Help page, ? icon)
- Security: [SECURITY.md](SECURITY.md)
- Checks that need a person: [docs/MANUAL-TESTS.md](docs/MANUAL-TESTS.md)
- Plans: `docs/PRD-*.md`

## Disclaimer

YourTube is unofficial and is not affiliated with or endorsed by YouTube or Google.

- It uses yt-dlp, which YouTube changes can break.
- It reads your own account with your own browser sign-in, at your own risk.
- It never changes anything on YouTube (read-only).

## License

This project is licensed under the [MIT License](LICENSE). For security practices and reporting, see [SECURITY.md](SECURITY.md).


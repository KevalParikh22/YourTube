# Security Policy

YourTube runs locally on your machine. Your library, transcripts, and credentials remain under your control.

## Cookies

- **When cookies are used:** Cookies are only read to list playlists (for private playlists or Watch Later).
- **Where they come from:** Read locally from your browser or a `cookies.txt` file.
- **Where they go:** Sent only to `youtube.com` via yt-dlp; they are never stored or logged.
- **Per-video reads:** Video details, chapters, captions, and transcripts are fetched anonymously without cookies.

## API keys

- **Storage in `settings.json`:** API keys live in `settings.json`, created readable only by you (owner-only permissions).
- **Alternative (environment variables):** Environment variables (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`) are the alternative. Use them on NTFS/FAT drives, where file permissions aren't enforced.
- **Never sent to the browser:** API keys are never sent to the browser (the dashboard API only reports whether a key is saved or set in the environment).

## The dashboard

- **Local only:** Listens only on `127.0.0.1` (`localhost`), so nothing is exposed to your local network or the internet.
- **Access token:** Needs the access token to access the dashboard or its API.
- **Host header:** Checks the `Host` header on incoming requests to prevent DNS rebinding.
- **Write checks:** Checks the `Host` header and the `X-YTKB` header on writes (`POST` requests) to protect against cross-site requests.
- **Logs:** `logs/yourtube.log` records failures to help with debugging. API keys, cookie headers and the access token are replaced with `[hidden]` before anything is written. When run as a background service, the access link is not printed to the service output.

## AI data privacy

- **Cloud AI:** Only titles, descriptions, transcripts, and questions go to the AI provider you choose.
- **Local AI:** With Local only mode, nothing leaves the computer.

## Reporting a problem

If you find a security issue or vulnerability:
- Open a GitHub issue without secrets (do not include cookies, tokens, or API keys in the issue or logs).
- Or contact the maintainer through GitHub.

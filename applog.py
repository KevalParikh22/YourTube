"""Redacted, best-effort file logging for the local dashboard."""
import logging
import os
import re
from logging.handlers import RotatingFileHandler

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.environ.get("YTKB_DATA_DIR") or ROOT_DIR
LOG_DIR = os.environ.get("YTKB_LOG_DIR") or os.path.join(_DATA_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "yourtube.log")
_secrets = set()

_REDACTIONS = (
    re.compile(r"(?i)([?&]t=)[^&#\s]+"),
    re.compile(r"(?i)([?&](?:api_key|key|token)=)[^&#\s]+"),
    re.compile(r'(?i)(["\'](?:api_key|key|token)["\']\s*:\s*["\'])[^"\']*'),
    re.compile(r"(?i)\b(Authorization|Cookie|x-api-key)\s*:\s*[^\r\n]+"),
    re.compile(r"sk-ant-[\w-]{10,}|sk-[\w-]{20,}|gsk_[\w-]{20,}|AIza[\w-]{20,}"),
)


def set_secret(value):
    if isinstance(value, str) and value:
        _secrets.add(value)


def redact(text):
    text = str(text)
    for pattern in _REDACTIONS:
        if pattern.groups:
            text = pattern.sub(lambda match: match.group(1) + "[hidden]", text)
        else:
            text = pattern.sub("[hidden]", text)
    for secret in sorted(_secrets, key=len, reverse=True):
        text = text.replace(secret, "[hidden]")
    return text


KNOWN = [
    # Before "playlist does not exist": these messages contain it but happen with a sign-in
    (
        re.compile(r"rotated the \w+ sign-in", re.IGNORECASE),
        "YouTube replaced the browser's sign-in while it was being read, so the private playlist looked missing",
        "Open youtube.com in that browser, reload the page, wait a minute, then sync again; or use a cookies.txt export",
    ),
    (
        re.compile(r"none of the browsers signed in to YouTube can read", re.IGNORECASE),
        "No browser on this computer is signed in to the account that owns this playlist",
        "Sign in to YouTube with that account (or switch to its channel) in one browser and sync again; or make the playlist Unlisted",
    ),
    (
        re.compile(r"playlist does not exist|This playlist type is unviewable|Could not extract info from playlist", re.IGNORECASE),
        "The playlist ID is wrong, or the playlist is private and was read without your sign-in",
        'Check the link. For private playlists and Watch Later choose "Use my browser login" or a cookies.txt file',
    ),
    (
        re.compile(r"Sign in to confirm you.?re not a bot", re.IGNORECASE),
        "YouTube asked for a sign-in check",
        "Sync with your browser login, wait a few minutes, or update yt-dlp: .venv/bin/python -m pip install -U yt-dlp",
    ),
    (
        re.compile(r"HTTP Error 429|Too Many Requests", re.IGNORECASE),
        "YouTube is rate-limiting this computer",
        "Wait 15–30 minutes, then try again",
    ),
    (
        re.compile(r"could not find .*cookies database|failed to decrypt|secretstorage", re.IGNORECASE),
        "The browser's cookies could not be read",
        "Sign in to YouTube in that browser, close it, and try again; on Linux install secretstorage; or use a cookies.txt export",
    ),
    (
        re.compile(r"Unsupported URL|is not a valid URL", re.IGNORECASE),
        "The link is not a YouTube playlist or video",
        "Paste a link like https://www.youtube.com/playlist?list=…",
    ),
    (
        re.compile(r"Connection refused.*11434|11434.*Connection refused|Ollama[\s\S]*not running|not running[\s\S]*Ollama", re.IGNORECASE),
        "Ollama is not running",
        "Start Ollama (ollama serve)",
    ),
    (
        re.compile(r"getaddrinfo|Name or service not known|timed out|Connection (?:refused|reset)|URLError", re.IGNORECASE),
        "No connection to YouTube",
        "Check the internet connection and try again",
    ),
    (
        re.compile(r"nsig extraction failed|Unable to extract|Requested format is not available", re.IGNORECASE),
        "YouTube changed something that yt-dlp does not handle yet",
        "Update yt-dlp: .venv/bin/python -m pip install -U yt-dlp",
    ),
    (
        re.compile(r"database is locked", re.IGNORECASE),
        "Another program is using the library database",
        "Close other YourTube windows or scripts and try again",
    ),
    (
        re.compile(r"No space left on device", re.IGNORECASE),
        "The disk is full",
        "Free some space",
    ),
]


def explain(output):
    if not output:
        return None
    text = str(output)
    return next(((cause, fix) for pattern, cause, fix in KNOWN if pattern.search(text)), None)


class RedactFilter(logging.Filter):
    def filter(self, record):
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))


def setup(level=logging.INFO):
    logger = logging.getLogger("yourtube")
    logger.setLevel(level)
    logger.propagate = False
    if any(getattr(handler, "_yourtube_file_handler", False) for handler in logger.handlers):
        return logger
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        handler = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        handler._yourtube_file_handler = True
        handler.addFilter(RedactFilter())
        handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                                                datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(handler)
    except Exception as exc:
        if not any(getattr(handler, "_yourtube_null_handler", False) for handler in logger.handlers):
            fallback = logging.NullHandler()
            fallback._yourtube_null_handler = True
            logger.addHandler(fallback)
            try:
                print(f"Warning: Could not create log file: {exc}")
            except OSError:
                pass
    return logger


def get(name):
    return logging.getLogger("yourtube." + name)


def log_task(kind, label, ok, stopped, started_at, finished_at, output):
    logger = get("job")
    try:
        from datetime import datetime
        start = datetime.fromisoformat(started_at)
        finish = datetime.fromisoformat(finished_at)
        duration = max(0.0, (finish - start).total_seconds())
    except (TypeError, ValueError):
        duration = 0.0
    prefix = f"{label} (kind={kind}, {duration:.1f} s)"
    lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
    if ok:
        logger.info("Task done: %s", prefix)
        for line in lines:
            if line.startswith("Warning:"):
                logger.warning("Task warning: %s: %s", label, line)
    elif stopped:
        logger.warning("Task stopped: %s", prefix)
    else:
        # only the error lines: the printed troubleshooting tips mention causes too
        explained = explain("\n".join(l for l in lines if l.lower().startswith(("error", "unexpected error"))))
        if explained:
            cause, fix = explained
        else:
            cause = next((line for line in reversed(lines) if line.startswith("Error:")), None) or "See the output below"
            fix = "See the output below"
        details = "\n".join("    " + line for line in lines[-40:]) or "    (no output)"
        logger.error("Task failed: %s\n  Cause: %s\n  Fix: %s\n  Last output:\n%s",
                     prefix, cause, fix, details)

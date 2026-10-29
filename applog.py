"""Redacted, best-effort file logging for the local dashboard."""
import logging
import os
import re
from logging.handlers import RotatingFileHandler

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.environ.get("YTKB_LOG_DIR") or os.path.join(ROOT_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "yourtube.log")
_secrets = set()

_REDACTIONS = (
    re.compile(r"(?i)([?&]t=)[^&#\s]+"),
    re.compile(r"(?i)([?&](?:api_key|key|token)=)[^&#\s]+"),
    re.compile(r'(?i)(["\'](?:api_key|key|token)["\']\s*:\s*["\'])[^"\']*'),
    re.compile(r"(?i)\b(Authorization|Cookie|x-api-key)\s*:\s*[^\r\n]+"),
    re.compile(r"sk-ant-[\w-]{10,}|sk-[\w-]{20,}|AIza[\w-]{20,}"),
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
        error = next((line for line in reversed(lines) if line.startswith("Error:")), None)
        details = "\n".join("    " + line for line in lines[-40:]) or "    (no output)"
        logger.error("Task failed: %s\n  Cause: %s\n  Fix: See the output below\n  Last output:\n%s",
                     prefix, error or "See the output below", details)

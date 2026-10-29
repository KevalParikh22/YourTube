"""
AI rewrite of learner interests for About you.

Takes the user's rough spoken or typed answer and turns it into clear, concise
learning interests (topics, level, what to skip, and goals) while keeping what still
applies from their existing interests. The output is proposed to the user as a
suggestion and is never saved automatically.
"""
import json
import re
from typing import Any, Callable, Dict, List, Optional

MIN_INPUT_CHARS = 3
MAX_INPUT_CHARS = 4000
MAX_OUTPUT_CHARS = 2000
MIN_REPLY_WORDS = 3

SYSTEM = (
    "You turn a learner's rough spoken or typed thoughts into clear, concise learning interests for YourTube.\n"
    "Reply with plain text only, no Markdown headings, in four short labelled lines (400–900 characters in total):\n"
    "Learn: <topics, concrete and specific>\n"
    "Level: <beginner / intermediate / advanced, per area if mixed>\n"
    "Skip: <what is not wanted; \"nothing specific\" if none>\n"
    "Goal: <why: job, project, exam, curiosity; \"not stated\" if the user did not say>\n"
    "Do not reply in JSON or with code fences.\n\n"
    "Rules:\n"
    "- Write in the same language as the user's text.\n"
    "- Use only what the user said or what the current interests say; never invent topics.\n"
    "- Keep the user's own terms (tool names, frameworks, languages).\n"
    "- The text may come from speech recognition: fix obvious slips (for example \"iagents\" means \"AI agents\").\n"
    "- The library's categories are context for vocabulary and spelling, not topics to add on your own.\n"
    "- Keep what still applies from current interests and fold in the new answer."
)


def _build_prompt(text: str, current: str, context: Optional[Dict[str, Any]] = None) -> str:
    ctx = context or {}
    raw_cats = ctx.get("categories") or []
    if isinstance(raw_cats, dict):
        raw_cats = list(raw_cats.keys())
    cats = [str(c) for c in raw_cats if c]
    cats_str = ", ".join(cats) if cats else "(none)"

    cur = (current or "").strip()
    cur_str = cur if cur else "(none)"

    lines = []
    lib = ctx.get("library")
    if lib:
        lines.append(f"Library: {lib}")
    lang = ctx.get("language")
    if lang:
        lines.append(f"Preferred language: {lang}")
    lines.append(f"Library categories (for vocabulary context only):\n{cats_str}")
    lines.append(f"Current interests:\n{cur_str}")
    lines.append(f"User's new text:\n{text}")
    return "\n\n".join(lines)


def _clean_reply(raw: str) -> str:
    text = (raw or "").strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    changed = True
    while changed:
        before = text
        text = re.sub(r"^`{3,}[a-zA-Z0-9_-]*\s*\n?", "", text).strip()
        text = re.sub(r"\n?\s*`{3,}\s*$", "", text).strip()
        if (text.startswith('"""') and text.endswith('"""')) or (text.startswith("'''") and text.endswith("'''")):
            if len(text) >= 6:
                text = text[3:-3].strip()
        if len(text) >= 2 and ((text.startswith('"') and text.endswith('"')) or
                               (text.startswith("'") and text.endswith("'")) or
                               (text.startswith("“") and text.endswith("”"))):
            text = text[1:-1].strip()
        changed = (text != before)

    text = _from_json(text)
    text = re.sub(r"^`{3,}[a-zA-Z0-9_-]*\s*$", "", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _from_json(text: str) -> str:
    """Small local models sometimes answer {"Learn": ..., "Level": ...}; turn that into the labelled lines."""
    if not text.startswith("{"):
        return text
    try:
        data = json.loads(text)
    except ValueError:
        return text
    if not isinstance(data, dict):
        return text
    lines = []
    for key, value in data.items():
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        lines.append(f"{str(key).strip().capitalize()}: {str(value).strip()}")
    return "\n".join(lines)


def _cut_reply(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    idx = cut.rfind("\n")
    if idx > 0:
        return cut[:idx].rstrip()
    return cut.rstrip()


def draft_interests(
    text: str,
    current: str = "",
    context: Optional[Dict[str, Any]] = None,
    complete: Optional[Callable[[str, str], str]] = None,
) -> str:
    """Turn rough spoken or typed input into structured learning interests."""
    if not isinstance(text, str):
        raise ValueError("Tell us a little more about what you want to learn")
    text = text.strip()
    if len(text) < MIN_INPUT_CHARS:
        raise ValueError("Tell us a little more about what you want to learn")
    if len(text) > MAX_INPUT_CHARS:
        raise ValueError("Your answer is too long (4,000 characters at most)")
    if complete is None:
        raise ValueError("No AI completer provided")

    prompt = _build_prompt(text, current or "", context or {})
    reply = complete(SYSTEM, prompt)
    cleaned = _clean_reply(reply)
    if len(cleaned.split()) < MIN_REPLY_WORDS:
        raise ValueError("The AI returned nothing useful. Try again or write it yourself")
    return _cut_reply(cleaned, MAX_OUTPUT_CHARS)

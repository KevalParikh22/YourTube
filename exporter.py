"""
Export and playlist creation utilities.
Provides:
1. YouTube Multi-Video Queue URLs (watch_videos endpoint)
2. CSV export and Markdown notes
"""
import io
import csv
import json
from typing import List, Dict, Any

def generate_youtube_watch_queue_url(video_ids: List[str]) -> str:
    """
    Generates an instant YouTube anonymous playlist URL.
    Opening this URL creates a queue player in YouTube containing all specified videos.
    """
    if not video_ids:
        return ""
    joined_ids = ",".join(video_ids[:50])  # YouTube supports up to 50 IDs in this endpoint
    return f"https://www.youtube.com/watch_videos?video_ids={joined_ids}"

def generate_youtube_watch_queue_urls(video_ids: List[str], chunk_size: int = 50) -> List[str]:
    """Splits a long list into several 50-video queue URLs so nothing is silently dropped."""
    return [
        generate_youtube_watch_queue_url(video_ids[i:i + chunk_size])
        for i in range(0, len(video_ids), chunk_size)
    ]

EXPORT_FIELDS = ["video_id", "title", "channel", "category", "status", "duration_seconds", "url", "notes"]

def videos_to_csv(videos: List[Dict[str, Any]]) -> str:
    """Serialises videos to CSV text (UTF-8, header row included)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for v in videos:
        writer.writerow({k: v.get(k, "") for k in EXPORT_FIELDS})
    return buf.getvalue()

# --- Markdown notes (Phase 1, F7) -------------------------------------------------------
# One note per video: front matter for Obsidian/Dataview, a link to the video, the AI verdict,
# summary, key points and skim ranges as timestamped links, chapters and the user's notes.
# Full transcripts are deliberately left out (personal use only, see docs/PRD-Phase-1.md).

import re
import zipfile
import datetime as _dt


def _clock(sec: Any) -> str:
    sec = int(sec or 0)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def _at(video_id: str, sec: Any) -> str:
    return f"https://www.youtube.com/watch?v={video_id}&t={int(sec or 0)}s"

def _length(sec: Any) -> str:
    sec = int(sec or 0)
    return f"{sec // 3600}h {sec % 3600 // 60}m" if sec >= 3600 else f"{sec // 60}m"

def _yaml(value: Any) -> str:
    return json.dumps("" if value is None else value, ensure_ascii=False)  # JSON strings are valid YAML

def _md_text(text: Any) -> str:
    """Keeps user and AI text from being read as Markdown structure (headings, tables, links)."""
    return re.sub(r"([\\`*_\[\]<>|#])", r"\\\1", str(text or "")).strip()

def safe_filename(name: str, limit: int = 80) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", name or "").strip(" .")
    return (" ".join(name.split())[:limit].rstrip(" .") or "untitled")

def video_markdown(v: Dict[str, Any], details: Dict[str, Any] = None) -> str:
    """One video's note. `v` is a library row (storage.get_library); `details` from storage.get_details."""
    details = details or {}
    r = v.get("rating") or {}
    vid = v["video_id"]
    url = f"https://www.youtube.com/watch?v={vid}"
    uploaded = details.get("upload_date") or ""
    if len(uploaded) == 8:
        uploaded = f"{uploaded[:4]}-{uploaded[4:6]}-{uploaded[6:]}"
    tag = re.sub(r"[^\w-]+", "-", (v.get("category") or "").lower()).strip("-") or "uncategorized"
    front = [
        "---",
        f"title: {_yaml(v.get('title'))}",
        f"channel: {_yaml(v.get('channel'))}",
        f"url: {url}",
        f"category: {_yaml(v.get('category'))}",
        f"status: {v.get('status')}",
        f"duration_minutes: {int(v.get('duration_seconds') or 0) // 60}",
    ]
    if uploaded:
        front.append(f"uploaded: {uploaded}")
    if r:
        front += [f"rating: {r.get('score')}", f"verdict: {r.get('verdict')}"]
    front += [f"tags: [yourtube, {tag}]", "---", ""]

    out = front + [f"# {_md_text(v.get('title'))}", "",
                   f"[▶ Watch on YouTube]({url}) · {_md_text(v.get('channel'))} · {_length(v.get('duration_seconds'))}", ""]
    if r:
        out += ["## Verdict", "", f"**{r.get('score')} · {str(r.get('verdict', '')).capitalize()}**: {_md_text(r.get('reason'))}", ""]
        if r.get("tldr"):
            out += ["## Summary", "", _md_text(r["tldr"]), ""]
        if r.get("minutes"):
            out += ["## Minutes", ""]
            for minute in r["minutes"]:
                t = minute.get("t")
                label = f"[{_clock(t)}]({_at(vid, t)}) " if t is not None else ""
                out.append(f"- {label}{_md_text(minute.get('topic'))}")
                out.extend(f"  - {_md_text(note)}" for note in minute.get("notes") or [])
            out.append("")
        if r.get("takeaways"):
            out += ["## Takeaways", ""]
            out += [f"- {_md_text(item)}" for item in r["takeaways"]]
            out.append("")
        if r.get("breakdown"):
            out += ["## Why this score", ""]
            for key, label in (("depth", "Depth"), ("fit", "Fit"), ("value", "Time value"), ("freshness", "Freshness")):
                part = r["breakdown"].get(key)
                if part:
                    out.append(f"- **{label}: {part.get('score')}** — {_md_text(part.get('why'))}")
            calc = r.get("calc") or r["breakdown"].get("calc")
            if calc:
                f = calc.get("formula") or {}
                out.append(f"- Score: {calc.get('score')} = quality {calc.get('quality')} (depth {f.get('depth', 0) * 100:g}% + value {f.get('value', 0) * 100:g}% + freshness {f.get('freshness', 0) * 100:g}%) + interest {calc.get('interest')}")
            out.append("")
        if r.get("points"):
            out += ["## Key points", ""]
            out += [f"- [{_clock(p['t'])}]({_at(vid, p['t'])}) {_md_text(p['text'])}" if p.get("t") is not None
                    else f"- {_md_text(p['text'])}" for p in r["points"]]
            out.append("")
        if r.get("skim"):
            out += ["## Worth skimming", ""]
            out += [f"- [{_clock(x['start'])}–{_clock(x['end'])}]({_at(vid, x['start'])}) {_md_text(x.get('why'))}" for x in r["skim"]]
            out.append("")
    if details.get("chapters"):
        out += ["## Chapters", ""]
        out += [f"- [{_clock(c.get('start'))}]({_at(vid, c.get('start'))}) {_md_text(c.get('title'))}" for c in details["chapters"]]
        out.append("")
    if details.get("visuals"):
        out += ["## On screen", ""]
        for x in details["visuals"]:
            text = " ".join((x.get("text") or "").split())
            text = text if len(text) <= 200 else text[:200].rsplit(" ", 1)[0] + "…"
            line = " · ".join(_md_text(y) for y in (text, x.get("visual")) if y)
            out.append(f"- [{_clock(x['t'])}]({_at(vid, x['t'])}) {line}")
        out.append("")
    out += ["## My notes", "", (v.get("notes") or "").strip() or "_No notes yet._", ""]
    return "\n".join(out)

def category_markdown(category: str, names: List[str]) -> str:
    """Index note for a category, linking each video note (Obsidian [[wiki links]])."""
    lines = [f"# {_md_text(category)}", "", f"{len(names)} video{'s' if len(names) != 1 else ''} · exported "
             f"{_dt.date.today().isoformat()} from YourTube", ""]
    lines += [f"- [[{n}]]" for n in names]
    return "\n".join(lines) + "\n"

def markdown_zip(videos: List[Dict[str, Any]], details: Dict[str, Dict[str, Any]]) -> bytes:
    """A zip of notes: YourTube/<Category>/<Title>.md plus one index note per category."""
    buf = io.BytesIO()
    by_cat: Dict[str, List[str]] = {}
    used = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for v in videos:
            cat = safe_filename(v.get("category") or "Uncategorized")
            name = safe_filename(v.get("title"))
            if (cat, name.lower()) in used:          # two videos with the same title
                name = f"{name} ({v['video_id']})"
            used.add((cat, name.lower()))
            z.writestr(f"YourTube/{cat}/{name}.md", video_markdown(v, details.get(v["video_id"])))
            by_cat.setdefault(v.get("category") or "Uncategorized", []).append(name)
        for cat, names in by_cat.items():
            z.writestr(f"YourTube/{safe_filename(cat)}/_{safe_filename(cat)}.md", category_markdown(cat, names))
    return buf.getvalue()

def ask_markdown(thread: List[Dict[str, Any]]) -> str:
    """A saved conversation (storage.get_thread) as a note: [n] marks link to the cited moments, sources under each answer."""
    if isinstance(thread, dict):
        thread = [thread]
    first = thread[0]
    lines = ["---", f"question: {_yaml(first.get('question'))}", f"asked: {_yaml(first.get('created_at'))}",
             f"model: {_yaml(first.get('model'))}", "tags: [yourtube, question]", "---", ""]
    escape = lambda t: re.sub(r"([\\`*_\[\]<>|#])", r"\\\1", t)   # like _md_text, but keeps the spaces around marks
    for i, item in enumerate(thread):
        sources = {s["n"]: s for s in item.get("sources") or []}
        link = lambda n: (f"[\\[{n}\\]]({_at(sources[n]['video_id'], sources[n].get('t'))})" if n in sources else "")
        parts = re.split(r"(\[\d+\])", item.get("answer") or "")
        answer = "".join(link(int(p[1:-1])) if re.fullmatch(r"\[\d+\]", p) else escape(p) for p in parts)
        lines += [f"{'#' if i == 0 else '##'} {_md_text(item.get('question'))}", ""]
        if item.get("standalone"):
            lines += [f"*Searched for: {_md_text(item['standalone'])}*", ""]
        lines += [answer.strip(), "", "Sources:", ""]
        for n, src in sorted(sources.items()):
            at = f" @ {_clock(src['t'])}" if src.get("t") is not None else ""
            mark = "" if src.get("cited") else " (not cited)"
            lines.append(f"{n}. [{_md_text(src.get('title'))}{at}]({_at(src['video_id'], src.get('t'))}){mark}: "
                         f"{_md_text(' '.join((src.get('text') or '').split())[:300])}")
        lines.append("")
    return "\n".join(lines)

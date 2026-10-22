"""
AI watch ratings: for each video, is it worth the user's time?

One model call per video returns a score (0-10), a verdict (watch / skim / skip), a short
reason, the ranges worth skimming, a TL;DR and key points with timestamps, written in the
user's preferred language. The rating weighs content depth and quality, fit with the user's
interests, time against value, and freshness.

Ratings never change a video. The user accepts a verdict (e.g. "Accept all skips") in Triage.
`complete(system, prompt) -> text` comes from llm.make_completer(settings, "rate").
"""
import datetime as dt
import re
from typing import Any, Callable, Dict, List, Optional

from llm import LLMError, parse_json

VERDICTS = ("watch", "skim", "skip")
BLOCK_SECONDS = 30            # transcript lines are merged into blocks this long
CHAR_BUDGET_REMOTE = 200_000  # about 50k tokens of transcript
OUTPUT_TOKENS = 700           # rough output per rating, for cost estimates

SYSTEM = (
    "You help a busy learner decide which videos in their YouTube backlog deserve their time. "
    "Be candid: much of a typical backlog is hype, padding or covered by a short summary. "
    "Reply with JSON only."
)


def _clock(sec: float) -> str:
    sec = int(sec or 0)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def transcript_blocks(segments: List[Dict[str, Any]], block: int = BLOCK_SECONDS) -> List[Dict[str, Any]]:
    """Merges caption lines into ~30 s blocks: [{'start', 'text'}]."""
    out: List[Dict[str, Any]] = []
    for seg in segments:
        if out and seg['start'] - out[-1]['start'] < block:
            out[-1]['text'] += " " + seg['text']
        else:
            out.append({'start': seg['start'], 'text': seg['text']})
    return out

def fit_transcript(blocks: List[Dict[str, Any]], budget: int) -> (str, bool):
    """Renders blocks as '[m:ss] text' lines within a character budget, sampling evenly if needed."""
    lines = [f"[{_clock(b['start'])}] {b['text']}" for b in blocks]
    total = sum(len(l) + 1 for l in lines)
    if total <= budget:
        return "\n".join(lines), False
    keep = max(1, int(len(lines) * budget / total))
    step = len(lines) / keep
    return "\n".join(lines[int(i * step)] for i in range(keep)), True

def _date(upload_date: Optional[str], today: Optional[dt.date] = None) -> str:
    """'2026-05-23 (5 months ago)', or 'unknown'. The age is spelled out: a small model guessed "outdated" from the date alone."""
    try:
        d = dt.datetime.strptime(upload_date or "", "%Y%m%d").date()
    except ValueError:
        return "unknown"
    days = ((today or dt.date.today()) - d).days
    age = ("this month" if days < 31 else f"{days // 30} months ago" if days < 365
           else f"about {round(days / 365, 1):g} years ago")
    return f"{d.isoformat()} ({age})"


# Model-given ranges that describe parts to pass over, not parts worth watching
_FILLER = re.compile(r"\b(intro|introduction|introductory|fluff|filler|sponsor|outro|recap|padding|skip|skippable|ad|ads)\b", re.I)


def build_prompt(video: Dict[str, Any], segments: List[Dict[str, Any]], settings: Dict[str, Any],
                 budget: int = CHAR_BUDGET_REMOTE, today: Optional[dt.date] = None) -> (str, str):
    """Returns (prompt, basis) where basis is 'transcript' or 'metadata'."""
    today = today or dt.date.today()
    language = settings.get("language") or "English"
    presets = set(settings.get("presets") or [])
    interests = (settings.get("interests") or "").strip() or "(not stated; infer from their categories)"
    cats = ", ".join(f"{c} (key interest)" if c in presets else c for c in settings.get("_categories", [])) or "(none)"
    minutes = round((video.get('duration_seconds') or 0) / 60)
    desc = (video.get('description') or "").strip()[:1500]
    chapters = "\n".join(f"[{_clock(c.get('start'))}] {c.get('title')}" for c in video.get('chapters') or [])

    slides = video.get("slides") or []
    slide_text = ""
    if slides:
        blocks = [{"start": x["t"], "text": " · ".join(y for y in (" ".join((x.get("text") or "").split()), x.get("visual") or "") if y)}
                  for x in slides]
        slide_text, _ = fit_transcript(blocks, budget // 4)   # slides get at most a quarter of the room
        budget -= len(slide_text)
    if segments:
        text, sampled = fit_transcript(transcript_blocks(segments), budget)
        basis = "transcript"
        source = f"Transcript{' (evenly sampled excerpts)' if sampled else ''}:\n{text}"
    else:
        basis = "metadata"
        source = "No transcript is available. Judge from the title, channel, description and chapters, and be conservative."

    prompt = f"""Today is {today.isoformat()}.

The learner's interests: {interests}
Their library's categories: {cats}

Video
Title: {video.get('title')}
Channel: {video.get('channel')}
Category in their library: {video.get('category')}
Length: {minutes} min
Uploaded: {_date(video.get('upload_date'), today)}
Description: {desc or '(none)'}
Chapters:
{chapters or '(none)'}

{source}
{f"{chr(10)}On-screen text (slides, code, whiteboard), read from video frames:{chr(10)}{slide_text}{chr(10)}" if slide_text else ""}
Rate how worth watching this video is for this learner, from 0 to 10, weighing:
1. Depth and quality: real substance and accuracy, versus hype, clickbait or padding.
2. Fit with their interests and categories.
3. Time against value: does it earn its length, or would a summary be enough?
4. Freshness: for fast-moving topics, penalise content that is outdated today. Call it outdated only when the content itself
   relies on things that have since changed (old versions, prices, events). Age alone, or an unknown date, is not evidence.

Use the whole scale. 8 to 10: exceptional and clearly worth the full time for this learner. 5 to 7: useful, but parts or a summary may be enough.
Below 5: weak, off-topic, padded or outdated. A typical backlog has many videos below 5.

Verdict: "watch" (worth the full time), "skim" (only parts are worth it), or "skip" (not worth it, or the summary covers it).
worth_watching: for "skim", the 1 to 4 best parts TO WATCH, with what each one teaches. Never the intro, sponsor, recap or outro.
For "watch" and "skip", leave it empty.
Write reason (at most 20 words), tldr (at most 2 sentences) and points (3 to 7 key takeaways) in {language}.
Times: copy them exactly as written in the transcript or chapters, like "4:05" or "1:02:30" (hours:minutes:seconds). Only use times that appear there.

Reply with JSON in this shape (replace every <...>):
{{"score": <0-10>, "verdict": "<watch|skim|skip>", "reason": "<...>", "tldr": "<...>",
 "points": [{{"t": "<time>", "text": "<...>"}}],
 "worth_watching": [{{"start": "<time>", "end": "<time>", "why": "<what this part teaches>"}}]}}"""
    return prompt, basis


def parse_time(x: Any) -> Optional[int]:
    """'1:02:30' / '4:05' / '95' / 95 -> seconds. The prompt asks for clock strings: models mix up units with bare numbers."""
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return int(x)
    text = str(x or "").strip().strip("[]")
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return int(float(text))
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})(?:\.\d+)?", text)
    if not m:
        return None
    h, mnt, sec = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    return h * 3600 + mnt * 60 + sec if sec < 60 and (not m.group(1) or mnt < 60) else None

def validate(raw: Any, duration: int) -> Dict[str, Any]:
    """Normalises a model reply; raises LLMError if it has no usable score or verdict."""
    if not isinstance(raw, dict):
        raise LLMError("The rating was not a JSON object")
    try:
        score = round(max(0.0, min(10.0, float(raw.get("score")))), 1)
    except (TypeError, ValueError):
        score = None
    verdict = str(raw.get("verdict") or "").strip().lower()
    if verdict not in VERDICTS:
        if score is None:
            raise LLMError("The rating had neither a score nor a verdict")
        verdict = "watch" if score >= 7 else "skim" if score >= 4 else "skip"
    if score is None:
        score = {"watch": 8.0, "skim": 5.5, "skip": 2.0}[verdict]
    limit = duration if duration > 0 else 10 ** 6

    def sec(x: Any) -> Optional[int]:
        v = parse_time(x)
        return v if v is not None and 0 <= v <= limit else None

    points = []
    for p in raw.get("points") or []:
        if isinstance(p, dict) and str(p.get("text") or "").strip():
            points.append({"t": sec(p.get("t")), "text": " ".join(str(p["text"]).split())[:300]})
    skim = []
    for r in raw.get("worth_watching") or raw.get("skim") or []:
        if not isinstance(r, dict):
            continue
        start, end = sec(r.get("start")), sec(r.get("end"))
        why = str(r.get("why") or "")
        if start is None or end is None or end <= start or _FILLER.search(why):
            continue   # a part to pass over, not one to watch
        if duration > 0 and end - start >= 0.85 * duration:
            continue   # "the whole video" is not a skim
        if verdict != "skip":
            skim.append({"start": start, "end": end, "why": " ".join(str(r.get("why") or "").split())[:160]})
    if verdict == "skim" and not skim and score < 4:
        verdict = "skip"   # nothing marked worth watching, and weak overall
    return {
        "score": score, "verdict": verdict,
        "reason": " ".join(str(raw.get("reason") or "").split())[:240],
        "tldr": " ".join(str(raw.get("tldr") or "").split())[:600],
        "points": points[:7], "skim": sorted(skim, key=lambda r: r["start"])[:4],
    }


def rate_video(video: Dict[str, Any], segments: List[Dict[str, Any]], settings: Dict[str, Any],
               complete: Callable[[str, str], str], budget: int = CHAR_BUDGET_REMOTE) -> Dict[str, Any]:
    prompt, basis = build_prompt(video, segments, settings, budget=budget)
    rating = validate(parse_json(complete(SYSTEM, prompt)), video.get('duration_seconds') or 0)
    return {**rating, "basis": basis, "language": settings.get("language") or "English"}


def _context(settings: Dict[str, Any]) -> Dict[str, Any]:
    from classifier import load_taxonomy
    return {**settings, "_categories": list(load_taxonomy())}

def estimate(video_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """What a rating run would send: videos, approximate tokens and price for the chosen model."""
    import llm
    import storage

    settings = _context(llm.load_settings())
    budget = llm.context_chars(settings, "rate", CHAR_BUDGET_REMOTE)
    videos = [{**v, "slides": storage.get_visuals(v["video_id"])} for v in storage.videos_for_rating(video_ids)]
    chars = with_transcript = 0
    for v in videos:
        segments = storage.get_segments(v['video_id'])
        with_transcript += bool(segments)
        chars += len(build_prompt(v, segments, settings, budget=budget)[0]) + len(SYSTEM)
    tokens_in, tokens_out = chars // 4, OUTPUT_TOKENS * len(videos)
    return {"videos": len(videos), "with_transcript": with_transcript, "tokens_in": tokens_in, "tokens_out": tokens_out,
            "cost": llm.estimate_cost(settings, "rate", tokens_in, tokens_out),
            "uses": llm.describe(settings, "rate"), "ready": llm.is_configured(settings, "rate")}


def run_rate(video_ids: Optional[List[str]] = None, should_stop: Callable[[], bool] = lambda: False) -> bool:
    """Rates unrated, unwatched videos (or the given ones). Prints progress for the job log."""
    import llm
    import storage

    settings = _context(llm.load_settings())
    videos = [{**v, "slides": storage.get_visuals(v["video_id"])} for v in storage.videos_for_rating(video_ids)]
    print(f"Rating {len(videos)} videos with {llm.describe(settings, 'rate')} (in {settings.get('language') or 'English'}).")
    if not videos:
        print("   Nothing to rate.")
        return True
    try:
        complete = llm.make_completer(settings, "rate")
    except LLMError as e:
        print(f"Error: {e}")
        return False
    budget, model = llm.context_chars(settings, "rate", CHAR_BUDGET_REMOTE), llm.model_for(settings, "rate")
    no_transcript = sum(1 for v in videos if v.get('details_status') != 'done')
    if no_transcript:
        print(f"   {no_transcript} have no transcript, so they are rated from title and description only. "
              "Fetch video details first for better ratings.")
    counts = {"watch": 0, "skim": 0, "skip": 0}
    failed = 0
    for n, v in enumerate(videos, 1):
        if should_stop():
            print("Stopped. Ratings so far are saved.")
            break
        try:
            rating = rate_video(v, storage.get_segments(v['video_id']), settings, complete, budget=budget)
        except LLMError as e:
            failed += 1
            print(f"   [{n}/{len(videos)}] Warning: {v['title'][:60]}: {e}")
            if failed >= 3 and failed == n:
                print("Error: The first three ratings all failed. Check the AI settings.")
                return False
            continue
        storage.save_rating(v['video_id'], rating, model=model)
        counts[rating['verdict']] += 1
        print(f"   [{n}/{len(videos)}] {rating['score']:>4} {rating['verdict'].upper():<5} {v['title'][:60]}")
    print(f"{counts['watch']} watch, {counts['skim']} skim, {counts['skip']} skip"
          + (f", {failed} failed" if failed else "") + ". Review them in Triage.")
    return True

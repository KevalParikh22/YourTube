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

from exporter import _clock
from llm import LLMError, parse_json

VERDICTS = ("watch", "skim", "skip")
BLOCK_SECONDS = 30            # transcript lines are merged into blocks this long
CHAR_BUDGET_REMOTE = 200_000  # about 50k tokens of transcript
OUTPUT_TOKENS = 1100          # rough output per rating (breakdown, minutes, takeaways), for cost estimates
# The overall score is computed in code from four factor scores, so every number can be explained:
#   score = quality - penalty * clamp((FIT_OK - fit) / FIT_SPAN, 0, 1) + (bonus if fit >= FIT_BONUS)
# where quality is the weighted mean of depth, value and freshness (about the video itself), and fit
# (match with the learner's interests) only adjusts it: a great off-interest video still scores well.
WEIGHTS = {"depth": 0.45, "fit": 0.0, "value": 0.40, "freshness": 0.15}   # fit is applied as the adjustment below
DEFAULT_FORMULA = {"depth": 0.45, "value": 0.40, "freshness": 0.15, "interest_penalty": 2.0, "interest_bonus": 0.5}
FIT_OK, FIT_SPAN, FIT_BONUS = 7.0, 4.0, 9.0   # no penalty at fit >= 7, full penalty at fit <= 3, bonus at fit >= 9
FACTOR_LABEL = {"depth": "Depth and quality", "fit": "Fit with your interests", "value": "Time value", "freshness": "Freshness"}

SYSTEM = (
    "You help a busy learner decide which videos in their YouTube backlog deserve their time. "
    "Be candid: much of a typical backlog is hype, padding or covered by a short summary. "
    "Reply with JSON only."
)


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
    desc = (video.get('description') or "").strip()[:1500] if settings.get("use_descriptions", True) is not False else ""
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
Score four factors from 0 to 10, each with a short reason (at most 18 words) that names something specific in this video:
- depth: real substance and accuracy, versus hype, clickbait or padding. 8 or more only for expert-level, detailed content;
  generic tips or listicles are 3 to 5.
- fit: how well it matches the learner's stated interests. 9 or 10 only when the topic is named in their interests;
  at most 5 when their interests don't mention the topic at all.
- value: does it earn its length, or would these notes be enough?
- freshness: for fast-moving topics, is it outdated today? Call it outdated only when the content itself relies on things
  that have since changed (old versions, prices, events). Age alone, or an unknown date, is not evidence: then score 7.
Use the whole scale; most backlog videos are not 8 or more on every factor.

Verdict: "watch" (worth the full time), "skim" (only parts are worth it), or "skip" (not worth it, or the notes cover it).
worth_watching: for "skim", the 1 to 4 best parts TO WATCH, with what each one teaches. Never the intro, sponsor, recap or outro.
For "watch" and "skip", leave it empty.

Then write notes the learner can decide from without watching, like minutes of a meeting:
- minutes: 3 to 8 topics in time order. Each has the time it starts, a topic (at most 8 words) and 1 to 3 notes
  (at most 20 words each) saying what is actually said, claimed or shown, not "the video discusses X".
- takeaways: 3 to 5 concrete conclusions or actions.
- tldr: at most 2 sentences. reason: at most 20 words, why this verdict.
Write all text in {language}.
Times: copy them exactly as written in the transcript or chapters, like "4:05" or "1:02:30" (hours:minutes:seconds). Only use times that appear there.

Reply with JSON in this shape (replace every <...>):
{{"breakdown": {{"depth": {{"score": <0-10>, "why": "<...>"}}, "fit": {{"score": <0-10>, "why": "<...>"}},
               "value": {{"score": <0-10>, "why": "<...>"}}, "freshness": {{"score": <0-10>, "why": "<...>"}}}},
 "verdict": "<watch|skim|skip>", "reason": "<...>", "tldr": "<...>",
 "minutes": [{{"t": "<time>", "topic": "<...>", "notes": ["<...>"]}}],
 "takeaways": ["<...>"],
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

def _clamp(x: Any) -> Optional[float]:
    try:
        return round(max(0.0, min(10.0, float(x))), 1)
    except (TypeError, ValueError):
        return None

def _text(x: Any, limit: int) -> str:
    return " ".join(str(x or "").split())[:limit]

def breakdown_of(raw: Any) -> Dict[str, Dict[str, Any]]:
    """The factor scores the model gave: {factor: {'score', 'why'}}, only factors with a usable score."""
    out = {}
    for k in WEIGHTS:
        part = raw.get(k) if isinstance(raw, dict) else None
        if isinstance(part, (int, float, str)) and not isinstance(part, bool):
            part = {"score": part}
        if isinstance(part, dict) and _clamp(part.get("score")) is not None:
            out[k] = {"score": _clamp(part.get("score")), "why": _text(part.get("why"), 160)}
    return out

def formula_of(settings: Optional[Dict[str, Any]]) -> Dict[str, float]:
    """The scoring formula: DEFAULT_FORMULA with valid numbers from settings["rating_formula"] (weights 0-1, penalty 0-5, bonus 0-2)."""
    f = dict(DEFAULT_FORMULA)
    custom = (settings or {}).get("rating_formula") or {}
    limits = {"depth": 1, "value": 1, "freshness": 1, "interest_penalty": 5, "interest_bonus": 2}
    for k, hi in limits.items():
        try:
            f[k] = max(0.0, min(float(hi), float(custom[k])))
        except (KeyError, TypeError, ValueError):
            pass
    if f["depth"] + f["value"] + f["freshness"] <= 0:
        f.update({k: DEFAULT_FORMULA[k] for k in ("depth", "value", "freshness")})
    return f

def compute_score(breakdown: Dict[str, Dict[str, Any]], formula: Optional[Dict[str, float]] = None) -> Optional[Dict[str, Any]]:
    """
    {'score', 'quality', 'interest', 'formula'} from a complete breakdown, or None when a factor is missing.
    interest is the adjustment for fit: 0 down to -penalty, or +bonus for a strong match.
    """
    if any(k not in breakdown for k in WEIGHTS):
        return None
    f = formula or dict(DEFAULT_FORMULA)
    weights = {k: f[k] for k in ("depth", "value", "freshness")}
    quality = sum(w * breakdown[k]["score"] for k, w in weights.items()) / sum(weights.values())
    fit = breakdown["fit"]["score"]
    interest = -f["interest_penalty"] * max(0.0, min(1.0, (FIT_OK - fit) / FIT_SPAN))
    if fit >= FIT_BONUS:
        interest += f["interest_bonus"]
    return {"score": round(max(0.0, min(10.0, quality + interest)), 1), "quality": round(quality, 1),
            "interest": round(interest, 1) + 0.0, "formula": f}   # + 0.0 turns -0.0 into 0.0

def _verdict_for(score: float) -> str:
    return "watch" if score >= 7 else "skim" if score >= 4 else "skip"

def validate(raw: Any, duration: int, formula: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """Normalises a model reply; raises LLMError if it has no usable score or verdict."""
    if not isinstance(raw, dict):
        raise LLMError("The rating was not a JSON object")
    breakdown = breakdown_of(raw.get("breakdown"))
    calc = compute_score(breakdown, formula)
    score = calc["score"] if calc else None
    if score is None:   # older reply shape or a partial breakdown: the model's own number, else the mean of what exists
        score = _clamp(raw.get("score"))
        if score is None and breakdown:
            score = round(sum(b["score"] for b in breakdown.values()) / len(breakdown), 1)
    verdict = str(raw.get("verdict") or "").strip().lower()
    if verdict not in VERDICTS:
        if score is None:
            raise LLMError("The rating had neither a score nor a verdict")
        verdict = "watch" if score >= 7 else "skim" if score >= 4 else "skip"
    if score is None:
        score = {"watch": 8.0, "skim": 5.5, "skip": 2.0}[verdict]
    # The verdict must fit the score, except right at a boundary where the model's judgement can tip it
    expected = _verdict_for(score)
    boundary = {frozenset(("skim", "watch")): 7, frozenset(("skip", "skim")): 4}.get(frozenset((verdict, expected)))
    if verdict != expected and (boundary is None or abs(score - boundary) > 1):
        verdict = expected
    limit = duration if duration > 0 else 10 ** 6

    def sec(x: Any) -> Optional[int]:
        v = parse_time(x)
        return v if v is not None and 0 <= v <= limit else None

    minutes = []
    for m in raw.get("minutes") or []:
        if not isinstance(m, dict):
            continue
        t, topic = sec(m.get("t")), _text(m.get("topic"), 80)
        notes = [_text(n, 200) for n in (m.get("notes") or []) if isinstance(n, str) and n.strip()][:3]
        if t is not None and (topic or notes):
            minutes.append({"t": t, "topic": topic, "notes": notes})
    minutes = sorted(minutes, key=lambda m: m["t"])[:8]
    takeaways = [_text(x, 200) for x in (raw.get("takeaways") or []) if isinstance(x, str) and x.strip()][:5]
    points = []
    if minutes:   # the older single-line points, for anything that still reads them
        points = [{"t": m["t"], "text": m["topic"] or m["notes"][0]} for m in minutes]
    for p in raw.get("points") or [] if not minutes else []:
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
    reason = " ".join(str(raw.get("reason") or "").split())[:240]
    if not reason and breakdown:   # the model left it out: say what drove the score
        hi = max(breakdown, key=lambda k: breakdown[k]["score"])
        lo = min(breakdown, key=lambda k: breakdown[k]["score"])
        reason = f"Strongest: {FACTOR_LABEL[hi].lower()} ({breakdown[hi]['score']:g}). Weakest: {FACTOR_LABEL[lo].lower()} ({breakdown[lo]['score']:g})."
    return {
        "score": score, "verdict": verdict,
        "reason": reason,
        "tldr": " ".join(str(raw.get("tldr") or "").split())[:600],
        "points": points[:8], "skim": sorted(skim, key=lambda r: r["start"])[:4],
        "breakdown": breakdown, "calc": calc, "minutes": minutes, "takeaways": takeaways,
    }


def rate_video(video: Dict[str, Any], segments: List[Dict[str, Any]], settings: Dict[str, Any],
               complete: Callable[[str, str], str], budget: int = CHAR_BUDGET_REMOTE) -> Dict[str, Any]:
    prompt, basis = build_prompt(video, segments, settings, budget=budget)
    rating = validate(parse_json(complete(SYSTEM, prompt)), video.get('duration_seconds') or 0, formula_of(settings))
    return {**rating, "basis": basis, "language": settings.get("language") or "English"}


def _context(settings: Dict[str, Any], library: Optional[int] = None) -> Dict[str, Any]:
    """Settings as one library sees them (its interests and presets) plus its category names."""
    import llm
    from classifier import load_taxonomy
    return {**llm.for_library(settings, library), "_categories": list(load_taxonomy(library=library))}

def _contexts(settings: Dict[str, Any]):
    """context(video) -> the rating context of the video's library, built once per library."""
    import storage
    libs = storage.video_libraries()
    cache = {lib: _context(settings, lib) for lib in set(libs.values()) or {1}}
    def context(video):
        lib = libs.get(video["video_id"], 1)
        if lib not in cache:
            cache[lib] = _context(settings, lib)
        return cache[lib]
    return context

def _rate_one(video: Dict[str, Any], complete: Callable[[str, str], str], budget: int, model: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """Rates a single video, saves to DB and returns the rating dict."""
    import storage
    rating = rate_video(video, storage.get_segments(video['video_id']), context, complete, budget=budget)
    storage.save_rating(video['video_id'], rating, model=model)
    return rating

def estimate(video_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """What a rating run would send: videos, approximate tokens and price for the chosen model."""
    import llm
    import storage

    settings = llm.load_settings()
    context = _contexts(settings)
    budget = llm.context_chars(settings, "rate", CHAR_BUDGET_REMOTE)
    videos = [{**v, "slides": storage.get_visuals(v["video_id"])} for v in storage.videos_for_rating(video_ids)]
    chars = with_transcript = 0
    for v in videos:
        segments = storage.get_segments(v['video_id'])
        with_transcript += bool(segments)
        chars += len(build_prompt(v, segments, context(v), budget=budget)[0]) + len(SYSTEM)
    tokens_in, tokens_out = chars // 4, OUTPUT_TOKENS * len(videos)
    cost = llm.estimate_cost(settings, "rate", tokens_in, tokens_out)
    uses = llm.describe(settings, "rate")
    helper = llm.helper_for(settings, "rate")
    cost_note = None
    if helper:
        helper_settings = dict(settings, tasks={**settings.get("tasks", {}), "rate": helper})
        cloud_cost = llm.estimate_cost(helper_settings, "rate", tokens_in, tokens_out)
        uses = f"{uses} + {llm.describe(helper_settings, 'rate')}"
        if cost == 0.0 and cloud_cost is not None:
            cost = cloud_cost
            cost_note = "less if the local model keeps up"
        elif cloud_cost == 0.0 and cost is not None:
            cost_note = "less if the local model keeps up"
    out = {"videos": len(videos), "with_transcript": with_transcript, "tokens_in": tokens_in, "tokens_out": tokens_out,
           "cost": cost, "uses": uses, "ready": llm.is_configured(settings, "rate")}
    if cost_note:
        out["cost_note"] = cost_note
    return out


def run_rate(video_ids: Optional[List[str]] = None, should_stop: Callable[[], bool] = lambda: False) -> bool:
    """Rates unrated, unwatched videos (or the given ones). Prints progress for the job log."""
    import threading
    import llm
    import parallel
    import storage

    settings = llm.load_settings()
    context = _contexts(settings)
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

    helper = llm.helper_for(settings, "rate")
    if not helper:
        counts = {"watch": 0, "skim": 0, "skip": 0}
        failed = 0
        for n, v in enumerate(videos, 1):
            if should_stop():
                print("Stopped. Ratings so far are saved.")
                break
            try:
                rating = _rate_one(v, complete, budget, model, context(v))
            except LLMError as e:
                failed += 1
                print(f"   [{n}/{len(videos)}] Warning: {v['title'][:60]}: {e}")
                if failed >= 3 and failed == n:
                    print("Error: The first three ratings all failed. Check the AI settings.")
                    return False
                continue
            counts[rating['verdict']] += 1
            print(f"   [{n}/{len(videos)}] {rating['score']:>4} {rating['verdict'].upper():<5} {v['title'][:60]}")
        print(f"{counts['watch']} watch, {counts['skim']} skim, {counts['skip']} skip"
              + (f", {failed} failed" if failed else "") + ". Review them in Triage.")
        return True

    # Helper AI: two workers share the batch
    helper_settings = dict(settings, tasks={**settings.get("tasks", {}), "rate": helper})
    try:
        helper_complete = llm.make_completer(helper_settings, "rate")
    except LLMError as e:
        print(f"Error: {e}")
        return False
    helper_budget = llm.context_chars(helper_settings, "rate", CHAR_BUDGET_REMOTE)
    helper_model = llm.model_for(helper_settings, "rate")

    main_label = llm.describe(settings, "rate")
    helper_label = llm.describe(helper_settings, "rate")
    if helper_label == main_label:
        helper_label = f"{helper_label} (helper)"
    main_tag = llm.model_for(settings, "rate")
    helper_tag = llm.model_for(helper_settings, "rate")

    progress_lock = threading.Lock()
    progress = 0
    counts = {"watch": 0, "skim": 0, "skip": 0}
    failed = 0

    def _make_worker(comp, bgt, mdl, tag):
        def _worker(v):
            nonlocal progress
            rating = _rate_one(v, comp, bgt, mdl, context(v))
            with progress_lock:
                progress += 1
                n = progress
                counts[rating['verdict']] += 1
                line = f"   [{n}/{len(videos)}] {rating['score']:>4} {rating['verdict'].upper():<5} {v['title'][:60]} · {tag}"
            parallel.safe_print(line)
        return _worker

    def _on_error(worker_name, item, exc):
        nonlocal progress, failed
        with progress_lock:
            progress += 1
            n = progress
            failed += 1
            line = f"   [{n}/{len(videos)}] Warning: {item['title'][:60]}: {exc}"
        parallel.safe_print(line)

    workers = {
        main_label: _make_worker(complete, budget, model, main_tag),
        helper_label: _make_worker(helper_complete, helper_budget, helper_model, helper_tag),
    }

    worker_counts = parallel.share(videos, workers, should_stop=should_stop, on_error=_on_error, retry_other=True)
    if should_stop():
        print("Stopped. Ratings so far are saved.")

    total_rated = sum(worker_counts.values())
    by_worker = ", ".join(f"{cnt} by {name}" for name, cnt in worker_counts.items())
    print(f"{total_rated} rated: {by_worker}.")
    print(f"{counts['watch']} watch, {counts['skim']} skim, {counts['skip']} skip"
          + (f", {failed} failed" if failed else "") + ". Review them in Triage.")
    return failed == 0 or total_rated > 0

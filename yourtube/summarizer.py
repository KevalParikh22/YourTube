"""
Video summaries (Phase 3): made when the user clicks Summarise, separate from the personal rating.
One model call per video returns a TL;DR, meeting-style minutes, key claims and quotes, check-yourself
questions, takeaways, and "learn more" (related videos from the library, YouTube searches for gaps).
Related moments ("also explained in X at 4:20") are found in code with the meaning index, not by the model.
"""
from typing import Any, Callable, Dict, List, Optional

from exporter import _clock
from llm import LLMError, parse_json
from rater import CHAR_BUDGET_REMOTE, fit_transcript, parse_time, transcript_blocks

OUTPUT_TOKENS = 1600          # rough output per summary, for cost estimates
CANDIDATES = 8                # related videos offered to the model for "learn more"
MOMENT_MIN_SIM = 0.6          # cosine (0.55 let loosely related news clips through) for "also explained in" (statement vs passage, qwen3-embedding)
MOMENTS_PER_TOPIC = 2
CLAIM_KINDS = ("claim", "quote", "number", "step")
RELATIONS = ("prerequisite", "deeper", "other_view", "same_topic")
PART_MIN, PART_TARGET, PART_MAX = 20 * 60, 30 * 60, 40 * 60   # seconds: course parts, and chunks of transcripts too long for one call
LIMITS = {"minutes": 10, "claims": 10, "quiz": 5, "takeaways": 5, "related": 4, "searches": 3}

SYSTEM = ("You write study notes for a busy learner from a YouTube video's transcript, so they can learn from it "
          "without watching, or decide which parts to watch. Be concrete and faithful to what is said. Reply with JSON only.")


def _text(x: Any, limit: int) -> str:
    return " ".join(str(x or "").split())[:limit]


def candidates(video_id: str, video: Dict[str, Any], segments: List[Dict[str, Any]],
               retrieve_fn: Optional[Callable[..., Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """The most related other videos in the same library: [{'video_id', 'title', 'channel', 'about'}]."""
    import retrieval
    import storage
    libs = storage.video_libraries()
    lib = libs.get(video_id, 1)
    query = f"{video.get('title', '')}. " + " ".join(s["text"] for s in segments)[:2000]
    try:
        pieces = (retrieve_fn or retrieval.retrieve)(query, k=40, filters={})["pieces"]
    except Exception:
        return []
    seen: List[str] = []
    for p in pieces:
        vid = p["video_id"]
        if vid != video_id and vid not in seen and libs.get(vid, 1) == lib:
            seen.append(vid)
    by_id = {v["video_id"]: v for v in storage.get_library() if v["video_id"] in seen[:CANDIDATES]}
    out = []
    for vid in seen[:CANDIDATES]:
        v = by_id.get(vid)
        if not v:
            continue
        s = storage.get_summary(vid)
        about = (s or {}).get("tldr") or (v.get("rating") or {}).get("tldr") or ""
        out.append({"video_id": vid, "title": v["title"], "channel": v.get("channel") or "", "about": _text(about, 240)})
    return out


def split_parts(duration: int, segments: List[Dict[str, Any]], chapters: List[Dict[str, Any]]) -> List[List[int]]:
    """
    [[start, end]] seconds of 20–40 min parts covering the video. Cuts at the chapter start nearest each 30 min mark when
    the video has chapters, else at the longest pause between caption lines (a likely topic change). A video of
    at most 40 min is one part.
    """
    duration = max(duration or 0, int(segments[-1]["start"]) + 1 if segments else 0)
    if len(chapters or []) > 1:
        cuts = {int(c["start"]): 0 for c in chapters if c.get("start")}
    else:   # value: the pause before that line
        cuts = {int(b["start"]): b["start"] - a["start"] for a, b in zip(segments, segments[1:])}
    out, start = [], 0
    while duration - start > PART_MAX:
        lo, hi = start + PART_MIN, min(start + PART_MAX, duration - PART_MIN)
        near = [t for t in cuts if lo <= t <= hi]
        target = start + PART_TARGET
        cut = max(near, key=lambda t: (cuts[t], -abs(t - target))) if near else target
        out.append([start, cut])
        start = cut
    return out + [[start, duration]]


PART_PROMPT = """This part will be one lesson of a course the learner follows over a few weeks. Write, in {language}:
- title: what this part is about, at most 8 words.
- summary: at most 3 sentences on what is said or shown and its conclusion.
- points: 3 to 6 key points in time order, each with the time it is said and at most 25 words.
- actions: 1 to 3 concrete things to do after watching (an exercise, a thing to try, a decision to make), at most 25 words each.
Times: copy them exactly as written in the transcript, like "4:05" or "1:02:30".

Reply with JSON in this shape (replace every <...>):
{{"title": "<...>", "summary": "<...>", "points": [{{"t": "<time>", "text": "<...>"}}], "actions": ["<...>"]}}

Now the video:
Video: {title} ({channel}), part {n} of {total}, {start}–{end}.

Transcript of this part:
{text}"""


def validate_part(raw: Any, start: int, end: int) -> Dict[str, Any]:
    """Normalises one part's reply; raises LLMError when it has neither a summary nor points."""
    if not isinstance(raw, dict):
        raise LLMError("The part summary was not a JSON object")
    points = []
    for p in raw.get("points") or []:
        t = parse_time(p.get("t")) if isinstance(p, dict) else None
        if t is not None and start - 60 <= t <= end + 60 and _text(p.get("text"), 300):
            points.append({"t": t, "text": _text(p.get("text"), 300)})
    summary = _text(raw.get("summary"), 600)
    if not summary and not points:
        raise LLMError("The part summary had no summary or points")
    return {"start": start, "end": end, "title": _text(raw.get("title"), 80), "summary": summary,
            "points": sorted(points, key=lambda p: p["t"])[:6],
            "actions": [_text(a, 200) for a in raw.get("actions") or [] if isinstance(a, str) and a.strip()][:3]}


def summarize_parts(video: Dict[str, Any], segments: List[Dict[str, Any]], settings: Dict[str, Any],
                    complete: Callable[[str, str], str], budget: int,
                    should_stop: Callable[[], bool] = lambda: False) -> List[Dict[str, Any]]:
    """One model call per 20–40 min part: [{'start', 'end', 'title', 'summary', 'points', 'actions'}]."""
    spans = split_parts(video.get("duration_seconds") or 0, segments, video.get("chapters") or [])
    out = []
    for n, (start, end) in enumerate(spans, 1):
        if should_stop():
            raise LLMError("Stopped")
        part = [x for x in segments if start <= x["start"] < end]
        text, _ = fit_transcript(transcript_blocks(part), budget)
        prompt = PART_PROMPT.format(title=video.get("title"), channel=video.get("channel"), n=n, total=len(spans),
                                    start=_clock(start), end=_clock(end), text=text or "(no words in this part)",
                                    language=settings.get("language") or "English")
        out.append(validate_part(parse_json(complete(SYSTEM, prompt)), start, end))
        print(f"      part {n}/{len(spans)} ({_clock(start)}–{_clock(end)}): {out[-1]['title']}")
    return out


def _parts_text(parts: List[Dict[str, Any]]) -> str:
    """Part summaries as transcript-like '[m:ss] ...' lines, for the whole-video summary of a long transcript."""
    lines = []
    for n, p in enumerate(parts, 1):
        lines.append(f"[{_clock(p['start'])}] Part {n}: {p['title']}. {p['summary']}")
        lines += [f"[{_clock(x['t'])}] {x['text']}" for x in p["points"]]
    return "\n".join(lines)


def build_prompt(video: Dict[str, Any], segments: List[Dict[str, Any]], settings: Dict[str, Any],
                 cands: List[Dict[str, Any]], budget: int = CHAR_BUDGET_REMOTE,
                 parts: Optional[List[Dict[str, Any]]] = None) -> str:
    language = settings.get("language") or "English"
    chapters = "\n".join(f"[{_clock(c.get('start'))}] {c.get('title')}" for c in video.get("chapters") or [])
    slides = video.get("slides") or []
    slide_text = ""
    if slides:
        blocks = [{"start": x["t"], "text": " · ".join(y for y in (_text(x.get("text"), 600), x.get("visual") or "") if y)} for x in slides]
        slide_text, _ = fit_transcript(blocks, budget // 5)
        budget -= len(slide_text)
    text, sampled = (_parts_text(parts), False) if parts else fit_transcript(transcript_blocks(segments), budget)
    cand_text = "\n".join(f"- id {c['video_id']}: {c['title']} ({c['channel']}){': ' + c['about'] if c['about'] else ''}" for c in cands) or "(none)"
    return f"""Write, in {language}:
- tldr: at most 3 sentences: what the video covers and its main conclusion.
- minutes: 3 to 10 topics in time order, like minutes of a meeting. Each has the time it starts, a topic (at most 8 words)
  and 1 to 3 notes (at most 20 words each) saying what is actually said, claimed or shown, never "the video discusses X".
- claims: 3 to 10 of the most important statements, each with its time and a kind: "claim" (an assertion), "quote"
  (copy the speaker's exact words), "number" (a figure or measurement) or "step" (an instruction). At most 30 words.
- quiz: 3 to 5 questions that check understanding, each with a short answer (at most 25 words) and the time it is answered.
- takeaways: 3 to 5 concrete conclusions or actions.
- learn_more.related: up to 4 of the listed library videos that help, by id, each with a relation: "prerequisite"
  (watch first), "deeper" (goes further), "other_view" (a different or opposing take) or "same_topic", and why (at most 15 words).
  Only use ids from the list; leave it empty if none fit.
- learn_more.searches: up to 3 YouTube searches (at most 8 words) for what this video assumes or leaves out and the
  library does not cover, each with why (at most 15 words).
Times: copy them exactly as written in the transcript, like "4:05" or "1:02:30". Only use times that appear there.

Reply with JSON in this shape (replace every <...>):
{{"tldr": "<...>",
 "minutes": [{{"t": "<time>", "topic": "<...>", "notes": ["<...>"]}}],
 "claims": [{{"t": "<time>", "kind": "<claim|quote|number|step>", "text": "<...>"}}],
 "quiz": [{{"q": "<...>", "a": "<...>", "t": "<time>"}}],
 "takeaways": ["<...>"],
 "learn_more": {{"related": [{{"video_id": "<id>", "relation": "<prerequisite|deeper|other_view|same_topic>", "why": "<...>"}}],
                "searches": [{{"query": "<...>", "why": "<...>"}}]}}}}

Now the video:
Video
Title: {video.get('title')}
Channel: {video.get('channel')}
Length: {round((video.get('duration_seconds') or 0) / 60)} min
Chapters:
{chapters or '(none)'}

{'Notes on each part of the video (summarised from its full transcript)' if parts else 'Transcript'}{' (evenly sampled excerpts)' if sampled else ''}:
{text}
{f"{chr(10)}On-screen text (slides, code), read from video frames:{chr(10)}{slide_text}{chr(10)}" if slide_text else ""}
Other videos in the learner's library that may relate:
{cand_text}"""


def validate(raw: Any, duration: int, candidate_ids: List[str]) -> Dict[str, Any]:
    """Normalises a model reply; raises LLMError when it has no minutes."""
    if not isinstance(raw, dict):
        raise LLMError("The summary was not a JSON object")
    limit = duration if duration > 0 else 10 ** 6

    def sec(x: Any) -> Optional[int]:
        v = parse_time(x)
        return v if v is not None and 0 <= v <= limit else None

    minutes = []
    for m in raw.get("minutes") or []:
        if isinstance(m, dict) and sec(m.get("t")) is not None:
            notes = [_text(n, 200) for n in (m.get("notes") or []) if isinstance(n, str) and n.strip()][:3]
            topic = _text(m.get("topic"), 80)
            if topic or notes:
                minutes.append({"t": sec(m.get("t")), "topic": topic, "notes": notes})
    if not minutes:
        raise LLMError("The summary had no minutes with valid times")
    claims = [{"t": sec(c.get("t")), "kind": c.get("kind") if c.get("kind") in CLAIM_KINDS else "claim", "text": _text(c.get("text"), 300)}
              for c in raw.get("claims") or [] if isinstance(c, dict) and _text(c.get("text"), 300)]
    quiz = [{"q": _text(q.get("q"), 200), "a": _text(q.get("a"), 200), "t": sec(q.get("t"))}
            for q in raw.get("quiz") or [] if isinstance(q, dict) and _text(q.get("q"), 200) and _text(q.get("a"), 200)]
    takeaways = [_text(x, 200) for x in raw.get("takeaways") or [] if isinstance(x, str) and x.strip()]
    lm = raw.get("learn_more") if isinstance(raw.get("learn_more"), dict) else {}
    related, seen = [], set()
    for r in lm.get("related") or []:
        if isinstance(r, dict) and r.get("video_id") in candidate_ids and r["video_id"] not in seen:
            seen.add(r["video_id"])
            related.append({"video_id": r["video_id"], "relation": r.get("relation") if r.get("relation") in RELATIONS else "same_topic",
                            "why": _text(r.get("why"), 120)})
    searches = [{"query": _text(s.get("query"), 80), "why": _text(s.get("why"), 120)}
                for s in lm.get("searches") or [] if isinstance(s, dict) and _text(s.get("query"), 80)]
    by_time = lambda xs: sorted(xs, key=lambda x: (x["t"] is None, x["t"] or 0))
    return {"tldr": _text(raw.get("tldr"), 600),
            "minutes": sorted(minutes, key=lambda m: m["t"])[:LIMITS["minutes"]],
            "claims": by_time(claims)[:LIMITS["claims"]], "quiz": quiz[:LIMITS["quiz"]],
            "takeaways": takeaways[:LIMITS["takeaways"]],
            "learn_more": {"related": related[:LIMITS["related"]], "searches": searches[:LIMITS["searches"]]}}


def too_long(video: Dict[str, Any], segments: List[Dict[str, Any]], budget: int) -> bool:
    """True when the transcript would be sampled in one call and the video is long enough to summarise in parts."""
    return len(split_parts(video.get("duration_seconds") or 0, segments, [])) > 1 and fit_transcript(transcript_blocks(segments), budget)[1]


def summarize_video(video: Dict[str, Any], segments: List[Dict[str, Any]], settings: Dict[str, Any],
                    complete: Callable[[str, str], str], cands: List[Dict[str, Any]], budget: int,
                    parts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    A transcript too long for one call is summarised in parts first (or uses `parts` already made, from a course);
    the summary then has 'parts' and is written from them instead of from sampled excerpts.
    """
    if not parts and too_long(video, segments, budget):
        parts = summarize_parts(video, segments, settings, complete, budget)
    prompt = build_prompt(video, segments, settings, cands, budget, parts)
    summary = validate(parse_json(complete(SYSTEM, prompt)), video.get("duration_seconds") or 0, [c["video_id"] for c in cands])
    return {**summary, "language": settings.get("language") or "English", **({"parts": parts} if parts else {})}


def _summarize_one(video: Dict[str, Any], complete: Callable[[str, str], str], budget: int, model: str, settings: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Summarises a single video, saves to DB and returns summary dict, or None if no segments."""
    import storage
    segments = storage.get_segments(video["video_id"])
    if not segments:
        return None
    made = storage.get_course(video["video_id"])
    parts = made["parts"] if made else None
    with storage.video_step(video["video_id"], "summarize", model):
        summary = summarize_video(video, segments, settings, complete, candidates(video["video_id"], video, segments), budget, parts)
        storage.save_summary(video["video_id"], summary, model=model)
        if summary.get("parts") and not made:
            storage.save_parts(video["video_id"], summary["parts"], model=model)
    return summary


def _videos(video_ids: List[str]) -> List[Dict[str, Any]]:
    import storage
    return [{**v, "slides": storage.get_visuals(v["video_id"])} for v in storage.videos_for_rating(video_ids)]


CHARS_PER_SECOND = 15         # speech is about 150 words a minute; for estimates before a transcript is fetched


def estimate_by_length(seconds: List[int], settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Like estimate, for videos not fetched yet: their transcript size guessed from their length."""
    import llm
    settings = settings or llm.load_settings()
    budget = llm.context_chars(settings, "summarize", CHAR_BUDGET_REMOTE)
    chars = sum(min((s or 0) * CHARS_PER_SECOND, budget) + len(SYSTEM) + 4000 for s in seconds)
    tokens_in, tokens_out = chars // 4, OUTPUT_TOKENS * len(seconds)
    return {"videos": len(seconds), "tokens_in": tokens_in, "tokens_out": tokens_out,
            "cost": llm.estimate_cost(settings, "summarize", tokens_in, tokens_out), "uses": llm.describe(settings, "summarize"),
            "ready": llm.is_configured(settings, "summarize")}


def estimate(video_ids: List[str]) -> Dict[str, Any]:
    """What a summary run would send: videos, approximate tokens and price."""
    import llm
    import storage
    settings = llm.load_settings()
    budget = llm.context_chars(settings, "summarize", CHAR_BUDGET_REMOTE)
    chars = with_transcript = 0
    videos = _videos(video_ids)
    for v in videos:
        segments = storage.get_segments(v["video_id"])
        with_transcript += bool(segments)
        chars += len(build_prompt(v, segments, settings, [], budget)) + len(SYSTEM) + 2000
    tokens_in, tokens_out = chars // 4, OUTPUT_TOKENS * len(videos)
    cost = llm.estimate_cost(settings, "summarize", tokens_in, tokens_out)
    uses = llm.describe(settings, "summarize")
    helper = llm.helper_for(settings, "summarize")
    cost_note = None
    if helper:
        helper_settings = dict(settings, tasks={**settings.get("tasks", {}), "summarize": helper})
        cloud_cost = llm.estimate_cost(helper_settings, "summarize", tokens_in, tokens_out)
        uses = f"{uses} + {llm.describe(helper_settings, 'summarize')}"
        if cost == 0.0 and cloud_cost is not None:
            cost = cloud_cost
            cost_note = "less if the local model keeps up"
        elif cloud_cost == 0.0 and cost is not None:
            cost_note = "less if the local model keeps up"
    out = {"videos": len(videos), "with_transcript": with_transcript, "tokens_in": tokens_in, "tokens_out": tokens_out,
           "cost": cost, "uses": uses, "ready": llm.is_configured(settings, "summarize")}
    if cost_note:
        out["cost_note"] = cost_note
    return out


def run_summarize(video_ids: List[str], should_stop: Callable[[], bool] = lambda: False,
                  done_since: Optional[str] = None) -> bool:
    """
    Summarises the given videos (those with a transcript). Prints progress for the job log.
    done_since: skips videos summarised since then (see rater.run_rate).
    """
    import threading
    import llm
    import parallel
    import storage
    settings = llm.load_settings()
    videos = _videos(video_ids)
    if done_since:
        made = storage.summary_times()
        videos = [v for v in videos if made.get(v["video_id"], "") < done_since]
    print(f"Summarising {len(videos)} videos with {llm.describe(settings, 'summarize')} (in {settings.get('language') or 'English'}).")
    try:
        complete = llm.make_completer(settings, "summarize")
    except LLMError as e:
        print(f"Error: {e}")
        return False
    budget, model = llm.context_chars(settings, "summarize", CHAR_BUDGET_REMOTE), llm.model_for(settings, "summarize")

    helper = llm.helper_for(settings, "summarize")
    if not helper:
        done = failed = 0
        for n, v in enumerate(videos, 1):
            if should_stop():
                print("Stopped. Summaries so far are saved.")
                break
            try:
                summary = _summarize_one(v, complete, budget, model, settings)
            except llm.QuotaError as e:
                print(f"Error: {e}\n   Stopped with {len(videos) - n + 1} of {len(videos)} videos not summarised. "
                      "The summaries so far are saved; Retry carries on with the rest.")
                return False
            except LLMError as e:
                failed += 1
                print(f"   [{n}/{len(videos)}] Warning: {v['title'][:60]}: {e}")
                continue
            if summary is None:
                print(f"   [{n}/{len(videos)}] Skipped (no transcript yet: fetch details or transcribe it first): {v['title'][:60]}")
                continue
            done += 1
            print(f"   [{n}/{len(videos)}] {len(summary['minutes'])} topics, {len(summary['claims'])} claims: {v['title'][:60]}")
        print(f"Summarised {done} videos" + (f", {failed} failed" if failed else "") + ".")
        return failed == 0 or done > 0

    # Helper AI: two workers share the batch
    helper_settings = dict(settings, tasks={**settings.get("tasks", {}), "summarize": helper})
    try:
        helper_complete = llm.make_completer(helper_settings, "summarize")
    except LLMError as e:
        print(f"Error: {e}")
        return False
    helper_budget = llm.context_chars(helper_settings, "summarize", CHAR_BUDGET_REMOTE)
    helper_model = llm.model_for(helper_settings, "summarize")

    main_label = llm.describe(settings, "summarize")
    helper_label = llm.describe(helper_settings, "summarize")
    if helper_label == main_label:
        helper_label = f"{helper_label} (helper)"
    main_tag = llm.model_for(settings, "summarize")
    helper_tag = llm.model_for(helper_settings, "summarize")

    progress_lock = threading.Lock()
    progress = 0
    done = failed = 0
    worker_done = {main_label: 0, helper_label: 0}

    def _make_worker(comp, bgt, mdl, tag, label):
        def _worker(v):
            nonlocal progress, done
            summary = _summarize_one(v, comp, bgt, mdl, settings)
            if summary is None:
                with progress_lock:
                    progress += 1
                    n = progress
                    line = f"   [{n}/{len(videos)}] Skipped (no transcript yet: fetch details or transcribe it first): {v['title'][:60]}"
                parallel.safe_print(line)
                return
            with progress_lock:
                progress += 1
                n = progress
                done += 1
                worker_done[label] += 1
                line = f"   [{n}/{len(videos)}] {len(summary['minutes'])} topics, {len(summary['claims'])} claims: {v['title'][:60]} · {tag}"
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
        main_label: _make_worker(complete, budget, model, main_tag, main_label),
        helper_label: _make_worker(helper_complete, helper_budget, helper_model, helper_tag, helper_label),
    }

    parallel.share(videos, workers, should_stop=should_stop, on_error=_on_error, retry_other=True)
    if should_stop():
        print("Stopped. Summaries so far are saved.")

    by_worker = ", ".join(f"{cnt} by {name}" for name, cnt in worker_done.items())
    print(f"Summarised {done} videos: {by_worker}." + (f", {failed} failed" if failed else "") + ".")
    return failed == 0 or done > 0


def related_moments(video_id: str, summary: Dict[str, Any], settings: Optional[Dict[str, Any]] = None,
                    embed_fn: Optional[Callable[[List[str]], List[List[float]]]] = None) -> Dict[int, List[Dict[str, Any]]]:
    """
    For each minute topic (by index), the best matching moments in other videos of the same library:
    {i: [{'video_id', 'title', 't', 'score'}]}. Empty without a meaning index.
    """
    import numpy as np
    import embedder
    import llm
    import retrieval
    import storage
    settings = settings or llm.load_settings()
    emb = embedder.embedder_for(settings)
    model = emb.model
    rows, mat = retrieval._matrix(model)
    minutes = summary.get("minutes") or []
    if not len(rows) or not minutes:
        return {}
    libs = storage.video_libraries()
    lib = libs.get(video_id, 1)
    keep = [i for i, r in enumerate(rows) if r["kind"] in ("transcript", "slide") and r["video_id"] != video_id
            and libs.get(r["video_id"], 1) == lib]
    if not keep:
        return {}
    texts = [f"{m['topic']}: {' '.join(m['notes'])}" for m in minutes]
    try:
        if embed_fn is None:
            embed_fn = emb.queries
        q = np.asarray(embed_fn(texts), dtype=np.float32)
    except Exception:
        return {}
    q /= np.linalg.norm(q, axis=1, keepdims=True) + 1e-12
    sims = q @ mat[keep].T
    titles = {v["video_id"]: v["title"] for v in storage.get_library()}
    out: Dict[int, List[Dict[str, Any]]] = {}
    for i, row in enumerate(sims):
        picked, videos_seen = [], set()
        for j in np.argsort(-row):
            if row[j] < MOMENT_MIN_SIM or len(picked) >= MOMENTS_PER_TOPIC:
                break
            r = rows[keep[j]]
            if r["video_id"] in videos_seen:
                continue
            videos_seen.add(r["video_id"])
            picked.append({"video_id": r["video_id"], "title": titles.get(r["video_id"], ""), "t": r["t"], "score": round(float(row[j]), 3)})
        if picked:
            out[i] = picked
    return out


def run_course(video_ids: List[str], per_week: int, should_stop: Callable[[], bool] = lambda: False) -> bool:
    """
    Makes each video a weekly course of `per_week` parts a week. Parts already made (by a long summary or an earlier
    course) are reused without an AI call. Prints progress for the job log.
    """
    import llm
    import storage
    settings = llm.load_settings()
    complete = None
    ok = True
    for v in _videos(video_ids):
        if should_stop():
            print("Stopped. Courses so far are saved.")
            return False
        if not storage.get_course(v["video_id"]):
            segments = storage.get_segments(v["video_id"])
            if not segments:
                print(f"   Skipped (no transcript yet: fetch details or transcribe it first): {v['title'][:60]}")
                ok = False
                continue
            print(f"   Splitting into parts: {v['title'][:60]}")
            try:
                complete = complete or llm.make_completer(settings, "summarize")
                parts = summarize_parts(v, segments, settings, complete,
                                        llm.context_chars(settings, "summarize", CHAR_BUDGET_REMOTE), should_stop)
            except LLMError as e:
                print(f"Error: {e}")
                return False
            storage.save_parts(v["video_id"], parts, model=llm.model_for(settings, "summarize"))
        storage.set_course_pace(v["video_id"], per_week)
        n = len(storage.get_course(v["video_id"])["parts"])
        print(f"   Course ready: {n} parts, {-(-n // per_week)} weeks at {per_week} a week: {v['title'][:60]}")
    return ok

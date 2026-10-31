"""
Knowledge pages (Phase 3): one page per category of a library, combining the summaries of its videos.
Every point cites numbered sources (a video, and a moment when there is one); citations are checked
in code. A page is stale when the category's summarised videos (or their summaries) change.
"""
import hashlib
import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from llm import LLMError, parse_json

SYSTEM = ("You write a study page that combines notes from several of the learner's YouTube videos on one topic. "
          "Use only the numbered sources. Every point must cite the sources it comes from as numbers. Reply with JSON only.")
MAX_SECTIONS, MAX_POINTS, MAX_QUESTIONS = 8, 8, 5


def _videos(library: int, category: str) -> List[Dict[str, Any]]:
    import storage
    return [v for v in storage.get_library() if v["library"] == library and v["category"] == category]


def summaries_hash(library: int, category: str) -> Tuple[str, List[Dict[str, Any]]]:
    """(hash of the summarised videos and their summary times, [(video, summary)]) for a category."""
    import storage
    pairs = [(v, storage.get_summary(v["video_id"])) for v in _videos(library, category)]
    pairs = [(v, s) for v, s in pairs if s]
    return _digest({v["video_id"]: s["created_at"] for v, s in pairs}), pairs


def _digest(times: Dict[str, str]) -> str:
    return hashlib.sha1("|".join(sorted(f"{vid}@{t}" for vid, t in times.items())).encode()).hexdigest()


def sources(pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]], budget: int) -> Tuple[List[Dict[str, Any]], bool]:
    """
    Numbered source items [{'n', 'video_id', 'title', 't', 'text'}] from summaries, newest videos in full; when the
    total exceeds budget, older videos keep only their TL;DR and takeaways. Returns (items, shortened).
    """
    def items_for(v, s, full):
        out = [(None, s.get("tldr"))]
        if full:
            out += [(m["t"], f"{m['topic']}: {' '.join(m.get('notes') or [])}") for m in s.get("minutes") or []]
            out += [(c.get("t"), c.get("text")) for c in s.get("claims") or []]
        out += [(None, t) for t in s.get("takeaways") or []]
        return [{"video_id": v["video_id"], "title": v["title"], "t": t, "text": x} for t, x in out if (x or "").strip()]

    ordered = sorted(pairs, key=lambda p: p[1]["created_at"] or "", reverse=True)
    full = [items_for(v, s, True) for v, s in ordered]
    size = lambda groups: sum(len(i["text"]) + len(i["title"]) + 20 for g in groups for i in g)
    shortened = False
    for k in range(len(ordered) - 1, -1, -1):          # shorten the oldest first until it fits
        if size(full) <= budget:
            break
        full[k] = items_for(*ordered[k], False)
        shortened = True
    flat = [i for g in full for i in g]
    for n, item in enumerate(flat, 1):
        item["n"] = n
    return flat, shortened


def _clock(t: Optional[int]) -> str:
    from exporter import _clock as c
    return c(t)


def build_prompt(category: str, items: List[Dict[str, Any]], language: str) -> str:
    lines = "\n".join(f"[{i['n']}] {i['title']}{' @ ' + _clock(i['t']) if i['t'] is not None else ''}: {i['text']}" for i in items)
    return f"""Topic: {category}

Sources (notes from the learner's videos):
{lines}

Write in {language} a study page on this topic that someone could learn from without watching:
- overview: at most 4 sentences.
- sections: 2 to {MAX_SECTIONS}, each with a heading and 2 to {MAX_POINTS} points. Each point is one sentence (at most 35 words)
  and cites the source numbers it is based on. Combine what different videos say; note where they disagree.
- open_questions: up to {MAX_QUESTIONS} things the videos disagree on or leave unanswered.
Cite only the numbers above. Do not add knowledge that is not in the sources.

Reply with JSON in this shape:
{{"overview": "<...>", "sections": [{{"heading": "<...>", "points": [{{"text": "<...>", "cites": [1, 3]}}]}}],
 "open_questions": ["<...>"]}}"""


def validate(raw: Any, count: int) -> Dict[str, Any]:
    """Keeps points with at least one valid citation; raises LLMError when nothing usable is left."""
    if not isinstance(raw, dict):
        raise LLMError("The page was not a JSON object")
    text = lambda x, n: " ".join(str(x or "").split())[:n]
    sections = []
    for s in raw.get("sections") or []:
        if not isinstance(s, dict):
            continue
        points = []
        for p in s.get("points") or []:
            if not isinstance(p, dict):
                continue
            cites = []
            for c in p.get("cites") or re.findall(r"\[(\d+)\]", str(p.get("text") or "")):
                try:
                    c = int(c)
                except (TypeError, ValueError):
                    continue
                if 1 <= c <= count and c not in cites:
                    cites.append(c)
            body = text(re.sub(r"\s*\[\d+\]", "", str(p.get("text") or "")), 400)
            if body and cites:
                points.append({"text": body, "cites": cites})
        if points and text(s.get("heading"), 80):
            sections.append({"heading": text(s.get("heading"), 80), "points": points[:MAX_POINTS]})
    if not sections:
        raise LLMError("The page had no points with valid citations")
    return {"overview": text(raw.get("overview"), 800), "sections": sections[:MAX_SECTIONS],
            "open_questions": [text(q, 300) for q in raw.get("open_questions") or [] if text(q, 300)][:MAX_QUESTIONS]}


def build_page(library: int, category: str, settings: Optional[Dict[str, Any]] = None,
               complete: Optional[Callable[[str, str], str]] = None) -> Dict[str, Any]:
    """Builds and saves the page; returns it. Raises LLMError (e.g. no summarised videos)."""
    import llm
    import storage
    settings = settings or llm.load_settings()
    digest, pairs = summaries_hash(library, category)
    if not pairs:
        raise LLMError("No video in this category has a summary yet. Summarise some first.")
    budget = max(4_000, llm.context_chars(settings, "summarize") - 6_000)
    items, shortened = sources(pairs, budget)
    complete = complete or llm.make_completer(settings, "summarize")
    page = validate(parse_json(complete(SYSTEM, build_prompt(category, items, settings.get("language") or "English"))), len(items))
    page["shortened"] = shortened
    storage.save_page(library, category, page, items, digest, llm.model_for(settings, "summarize"))
    return page


def status(library: int) -> List[Dict[str, Any]]:
    """Per category: {'category', 'videos', 'summarised', 'page': 'none'|'ready'|'stale', 'built_at'}."""
    import storage
    from classifier import FALLBACK_CATEGORY, load_taxonomy
    videos = [v for v in storage.get_library() if v["library"] == library]
    done = storage.summary_times()   # one query, not one library load per category
    pages = {p["category"]: p for p in storage.list_pages(library)}
    names = list(load_taxonomy(library=library)) + sorted({v["category"] for v in videos} - set(load_taxonomy(library=library)))
    out = []
    for name in names:
        in_cat = [v for v in videos if v["category"] == name]
        if not in_cat and name != FALLBACK_CATEGORY and name not in pages:
            continue
        page = pages.get(name)
        digest = _digest({v["video_id"]: done[v["video_id"]] for v in in_cat if v["video_id"] in done})
        state = "none" if not page else ("ready" if page["video_ids_hash"] == digest else "stale")
        out.append({"category": name, "videos": len(in_cat), "summarised": sum(v["video_id"] in done for v in in_cat),
                    "page": state, "built_at": page["created_at"] if page else None})
    return out


def run_build(library: int, category: str) -> bool:
    """Job entry point: prints progress for the job log."""
    print(f"Building the knowledge page for {category}…")
    try:
        page = build_page(library, category)
    except LLMError as e:
        print(f"Error: {e}")
        return False
    points = sum(len(s["points"]) for s in page["sections"])
    print(f"Knowledge page ready: {len(page['sections'])} sections, {points} cited points"
          + (" (older videos shortened to fit the model)" if page["shortened"] else "") + ".")
    return True


def page_markdown(category: str, page: Dict[str, Any], items: List[Dict[str, Any]]) -> str:
    """The page as an Obsidian-friendly note; citations link to the cited moments."""
    from exporter import _at, _md_text, _yaml
    by_n = {i["n"]: i for i in items}
    cite = lambda n: f"[\\[{n}\\]]({_at(by_n[n]['video_id'], by_n[n]['t'])})" if n in by_n else ""
    out = ["---", f"topic: {_yaml(category)}", f"built: {_yaml(page.get('created_at'))}", "tags: [yourtube, topic]", "---", "",
           f"# {_md_text(category)}", "", _md_text(page.get("overview")), ""]
    for s in page["sections"]:
        out += [f"## {_md_text(s['heading'])}", ""]
        out += [f"- {_md_text(p['text'])} {''.join(cite(n) for n in p['cites'])}" for p in s["points"]]
        out.append("")
    if page.get("open_questions"):
        out += ["## Open questions", ""] + [f"- {_md_text(q)}" for q in page["open_questions"]] + [""]
    out += ["## Sources", ""]
    seen = []
    for i in items:
        if i["video_id"] not in seen:
            seen.append(i["video_id"])
            out.append(f"- [{_md_text(i['title'])}]({_at(i['video_id'], 0)})")
    return "\n".join(out) + "\n"

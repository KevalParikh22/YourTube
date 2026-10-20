"""
AI categorisation: an LLM proposes a category set from the library (seeded by the user's
preset categories), then suggests one category per video. Nothing here writes to the
library. Proposals and suggestions are reviewed and accepted in the dashboard.

Both functions take `complete(system, prompt) -> text` (see llm.make_completer), so any
provider, or a fake in tests, can drive them.
"""
from typing import Any, Callable, Dict, List, Optional

from llm import LLMError, parse_json

FALLBACK_CATEGORY = "General / Other"
Complete = Callable[[str, str], str]

SAMPLE_LIMIT = 600   # titles shown to the model when proposing categories
BATCH_SIZE = 50

DISCOVER_SYSTEM = (
    "You organise a person's YouTube watch-later backlog into a small set of clear topic "
    "categories. Reply with JSON only."
)
ASSIGN_SYSTEM = (
    "You sort YouTube videos into a fixed list of categories using the title and channel. "
    "Reply with JSON only."
)


def _line(v: Dict[str, Any]) -> str:
    return f"{(v.get('title') or '').strip()} | {(v.get('channel') or '').strip()}"

def _sample(videos: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    if len(videos) <= limit:
        return videos
    step = len(videos) / limit
    return [videos[int(i * step)] for i in range(limit)]

def _clean_name(name: Any) -> str:
    return " ".join(str(name or "").split())[:60]


def discover_categories(
    videos: List[Dict[str, Any]],
    complete: Complete,
    presets: Optional[List[str]] = None,
    taxonomy: Optional[Dict[str, List[str]]] = None,
    max_categories: int = 12,
) -> List[Dict[str, Any]]:
    """
    Proposes a category set: every preset kept verbatim, plus new categories the library needs.
    Returns [{'name', 'description', 'keywords', 'preset', 'existing'}].
    """
    presets = [p for p in (presets or []) if p and p != FALLBACK_CATEGORY]
    taxonomy = taxonomy or {}
    if not videos:
        raise LLMError("The library is empty. Sync a playlist first.")

    sample = _sample(videos, SAMPLE_LIMIT)
    preset_text = "\n".join(f"- {p}" for p in presets) or "(none)"
    current = "\n".join(f"- {c}" for c in taxonomy if c not in presets) or "(none)"
    prompt = f"""Here are {len(sample)} videos (title | channel) from a library of {len(videos)}:

{chr(10).join(_line(v) for v in sample)}

Preset categories. Keep each one exactly as written:
{preset_text}

Categories in use now. Keep, rename, merge or drop them as the videos suggest:
{current}

Propose at most {max_categories} categories in total, presets included, that together cover most of these videos.
Prefer specific, useful topics over vague ones. Do not include a catch-all such as "{FALLBACK_CATEGORY}"; it always exists.
For each category give a one-sentence description and 5-12 lowercase keywords or short phrases that would appear in matching titles or channel names.

Reply with JSON:
{{"categories": [{{"name": "...", "description": "...", "keywords": ["...", "..."]}}]}}"""

    data = parse_json(complete(DISCOVER_SYSTEM, prompt))
    raw = data.get("categories") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise LLMError("The model's reply had no category list")

    out: Dict[str, Dict[str, Any]] = {}
    lower_presets = {p.lower(): p for p in presets}
    for c in raw:
        if not isinstance(c, dict):
            continue
        name = _clean_name(c.get("name"))
        if not name or name.lower() in (FALLBACK_CATEGORY.lower(), "uncategorized"):
            continue
        name = lower_presets.get(name.lower(), name)
        if name.lower() in (k.lower() for k in out):
            continue
        kws = c.get("keywords") if isinstance(c.get("keywords"), list) else []
        out[name] = {
            "name": name,
            "description": " ".join(str(c.get("description") or "").split())[:200],
            "keywords": sorted({str(k).strip().lower() for k in kws if str(k).strip()})[:15],
        }
    for p in presets:  # the model must not drop a preset
        out.setdefault(p, {"name": p, "description": "", "keywords": []})
    for c in out.values():
        c["preset"] = c["name"] in presets
        c["existing"] = c["name"] in taxonomy
        # Keep the user's own keywords alongside the model's
        c["keywords"] = sorted(set(c["keywords"]) | set(taxonomy.get(c["name"], [])))
    return list(out.values())


def assign_categories(
    videos: List[Dict[str, Any]],
    categories: List[str],
    complete: Complete,
    notes: Optional[Dict[str, str]] = None,
    batch_size: int = BATCH_SIZE,
    log: Callable[[str], None] = print,
) -> Dict[str, Any]:
    """
    Suggests one category per video, in batches. Unknown answers fall back to the catch-all.
    Returns {'suggestions': [{'video_id', 'category', 'confidence'}], 'failed_batches': n}.
    """
    names = [c for c in categories if c and c != FALLBACK_CATEGORY]
    if not names:
        raise LLMError("There are no categories to sort into. Add some first.")
    notes = notes or {}
    by_lower = {c.lower(): c for c in names + [FALLBACK_CATEGORY]}
    cat_text = "\n".join(f"- {c}" + (f": {notes[c]}" if notes.get(c) else "") for c in names)
    cat_text += f"\n- {FALLBACK_CATEGORY}: anything that fits none of the above"

    suggestions: Dict[str, Dict[str, Any]] = {}
    failed = 0
    batches = [videos[i:i + batch_size] for i in range(0, len(videos), batch_size)]
    for n, batch in enumerate(batches, 1):
        log(f"   🤖 Batch {n}/{len(batches)} ({len(batch)} videos)…")
        prompt = f"""Categories:
{cat_text}

Videos (number. title | channel):
{chr(10).join(f"{i}. {_line(v)}" for i, v in enumerate(batch, 1))}

Give every video exactly one category from the list, written exactly as listed, and your confidence from 0 to 1.
Reply with JSON:
{{"items": [{{"i": 1, "category": "...", "confidence": 0.9}}]}}"""
        try:
            data = parse_json(complete(ASSIGN_SYSTEM, prompt))
        except LLMError as e:
            failed += 1
            log(f"   ⚠️ Batch {n} failed: {e}")
            continue
        items = data.get("items") if isinstance(data, dict) else data
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict):
                continue
            try:
                idx = int(it.get("i")) - 1
            except (TypeError, ValueError):
                continue
            if not 0 <= idx < len(batch):
                continue
            category = by_lower.get(_clean_name(it.get("category")).lower(), FALLBACK_CATEGORY)
            try:
                confidence = max(0.0, min(1.0, float(it.get("confidence"))))
            except (TypeError, ValueError):
                confidence = None
            vid = batch[idx]["video_id"]
            suggestions[vid] = {"video_id": vid, "category": category, "confidence": confidence}
    if batches and failed == len(batches):
        raise LLMError("Every batch failed. Check the AI settings and try again.")
    return {"suggestions": list(suggestions.values()), "failed_batches": failed}


# --- runners used by the CLI and the dashboard job ---------------------------

def run_suggest(scope: str = "unsorted") -> bool:
    """Asks the configured model for category suggestions and stores them for review."""
    import llm
    import storage
    from classifier import load_taxonomy

    settings = llm.load_settings()
    videos = storage.videos_for_ai(scope)
    print(f"🤖 AI suggestions with {llm.describe(settings, 'categorize')}")
    if not videos:
        print("   Nothing to sort: no unlocked videos without a pending suggestion.")
        return True
    taxonomy = load_taxonomy()
    try:
        complete = llm.make_completer(settings, "categorize")
        print(f"   Sorting {len(videos)} videos into {len(taxonomy)} categories…")
        result = assign_categories(videos, list(taxonomy), complete, notes=settings.get("category_notes"))
    except LLMError as e:
        print(f"❌ {e}")
        return False
    model = llm.model_for(settings, "categorize")
    stored = storage.save_suggestions(result["suggestions"], model=model)
    same = len(result["suggestions"]) - stored
    print(f"✅ {stored} suggestion{'' if stored == 1 else 's'} ready for review"
          + (f", {same} already in the suggested category" if same else "")
          + (f", {result['failed_batches']} batches failed" if result["failed_batches"] else "") + ".")
    print("   Nothing changes until you accept them (Library → AI suggestions).")
    return True


def run_discover(max_categories: int = 12) -> bool:
    """Asks the configured model to propose a category set and stores it for review."""
    import llm
    import storage
    from classifier import load_taxonomy

    settings = llm.load_settings()
    videos = [v for v in storage.get_library() if v.get("status") not in storage.DONE_STATUSES] or storage.get_library()
    print(f"🤖 Proposing categories with {llm.describe(settings, 'categorize')} from {len(videos)} videos…")
    try:
        complete = llm.make_completer(settings, "categorize")
        cats = discover_categories(videos, complete, presets=settings.get("presets"),
                                   taxonomy=load_taxonomy(), max_categories=max_categories)
    except LLMError as e:
        print(f"❌ {e}")
        return False
    settings = llm.load_settings()  # re-read: the user may have saved settings meanwhile
    settings["proposal"] = {"categories": cats, "model": llm.describe(settings, "categorize")}
    llm.save_settings(settings)
    print(f"✅ Proposed {len(cats)} categories. Review them on the Categories page.")
    return True

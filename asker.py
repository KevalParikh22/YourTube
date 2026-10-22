"""
Questions across the library (Phase 2): hybrid retrieval picks the pieces, the "ask" AI writes a
short answer citing them as [n], and the citations are checked in code before anyone sees them:
- numbers that weren't sent are dropped;
- a citation whose excerpt matches its sentence much worse than another sent excerpt is moved to
  that excerpt (embedding similarity). The spike found the small local model does this now and then.
Answers are kept in ask_history.
"""
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional

TOP = 8                # pieces sent to the answer model (spike: rank-6 answers still reach it)
PIECE_CHARS = 1200     # longest excerpt sent; cut further when the model's context is small
SOURCE_CHARS = 600     # excerpt text kept with a saved answer
MOVE_MARGIN = 0.12     # move a citation when another excerpt matches its sentence this much better (cosine)
MOVE_FLOOR = 0.5       # ... and that excerpt matches it well in its own right (not two equally weak matches)
# Sentences are matched to excerpts like queries to documents (qwen3-embedding is instruction-aware)
CLAIM_PREFIX = "Instruct: Given a statement from an answer, retrieve the video passage it is based on\nQuery: "
NOTHING = "Nothing in your library matches this question yet. Try other words, or fetch details for more videos."
KIND_LABEL = {"title": "title", "chapter": "chapter", "transcript": "transcript", "slide": "on screen",
              "summary": "AI summary", "notes": "user's notes", "description": "description"}

SYSTEM = (
    "You answer questions using only the numbered excerpts from the user's own YouTube videos "
    "(transcripts, on-screen text, chapters, descriptions, AI summaries and the user's notes). "
    "Cite every claim with the number of the excerpt it comes from, in square brackets right after the sentence, "
    "like [2] or [1][4]. Use only what the excerpts say; never add outside knowledge. If the excerpts don't answer "
    "the question, say that your videos don't cover it, and mention anything close that they do cover. "
    "Don't mention the excerpts or their numbers in the text itself; only put the [n] marks after sentences. "
    "Keep it short: 2 to 6 sentences, or a short list when the question asks for several things. "
    'Reply as JSON: {"answer": "..."}'
)

_CITE = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")
_SENTENCE_END = re.compile(r"(?<=[.!?।。])\s+(?![\s\[])|\n+")
_lock = threading.Lock()   # one answer at a time: a local model can't run two at once anyway


def clock(sec: Optional[float]) -> str:
    sec = int(sec or 0)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def excerpts(pieces: List[Dict[str, Any]], budget: int) -> str:
    each = max(300, min(PIECE_CHARS, budget // max(1, len(pieces))))
    out = []
    for n, p in enumerate(pieces, 1):
        at = f" @ {clock(p['t'])}" if p.get("t") is not None else ""
        out.append(f"[{n}] {p.get('title', '')}{at} ({KIND_LABEL.get(p['kind'], p['kind'])}): {p['text'][:each]}")
    return "\n\n".join(out)


def _normalize(answer: str) -> str:
    """[1, 3] and [1;3] become [1][3]."""
    return _CITE.sub(lambda m: "".join(f"[{n}]" for n in re.split(r"\s*[,;]\s*", m.group(1))), answer)


def check_citations(answer: str, count: int,
                    sim: Optional[Callable[[List[str]], Optional[List[List[float]]]]] = None) -> Dict[str, Any]:
    """
    Fixes the [n] marks of an answer written from `count` excerpts, in place. sim(sentences) returns, per sentence,
    its similarity to each excerpt (or None to skip the move check). Returns {'answer', 'cited', 'dropped', 'moved'}.
    """
    text = _normalize(answer or "").strip()
    text = re.sub(r"\s*\n\s*((?:\[\d+\])+)[ \t]*(?=\n|$)", r" \1", text)   # marks on a line of their own belong above
    parts, seps = _SENTENCE_END.split(text), _SENTENCE_END.findall(text)
    marks = [[int(n) for n in re.findall(r"\[(\d+)\]", part)] for part in parts]
    plain = [re.sub(r"\s*\[\d+\]", "", part).strip() or " " for part in parts]
    scores = None
    if sim and any(1 <= n <= count for m in marks for n in m):
        try:
            scores = sim(plain)
        except Exception:
            scores = None   # no embeddings: invalid numbers are still dropped
    dropped = moved = 0
    out = []
    for i, part in enumerate(parts):
        row = list(scores[i]) if scores else None
        best = max(range(count), key=lambda j: row[j]) + 1 if row else None
        new: List[Optional[int]] = []
        for n in marks[i]:
            if not 1 <= n <= count:
                dropped += 1
                new.append(None)
                continue
            if row and n != best and row[best - 1] >= MOVE_FLOOR and row[n - 1] < row[best - 1] - MOVE_MARGIN:
                moved += 1
                n = best
            new.append(None if n in new else n)
        it = iter(new)
        fixed = re.sub(r"\[\d+\]", lambda m: (lambda n: f"[{n}]" if n else "")(next(it)), part)
        out.append(re.sub(r"[ \t]+([.,;:!?।])", r"\1", re.sub(r"[ \t]{2,}", " ", fixed)))
        marks[i] = [n for n in new if n]
    joined = "".join(p + (seps[i] if i < len(seps) else "") for i, p in enumerate(out)).strip()
    return {"answer": joined, "cited": sorted({n for m in marks for n in m}), "dropped": dropped, "moved": moved}


def _embedding_similarity(settings: Dict[str, Any], pieces: List[Dict[str, Any]]):
    """sim(sentences) against the sent pieces' stored vectors, or None when they aren't all embedded."""
    import numpy as np
    import embedder
    import retrieval
    model = embedder.model_name(settings)
    rows, mat = retrieval._matrix(model)
    index = {r["id"]: i for i, r in enumerate(rows)}
    if not pieces or any(p["id"] not in index for p in pieces):
        return None
    sent = mat[[index[p["id"]] for p in pieces]]
    base = settings["providers"]["ollama"].get("base_url")

    def sim(sentences: List[str]):
        v = np.asarray(embedder.embed([CLAIM_PREFIX + s for s in sentences], model, base), dtype=np.float32)
        v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-12
        return (v @ sent.T).tolist()
    return sim


def ask(question: str, filters: Optional[Dict[str, Any]] = None, settings: Optional[Dict[str, Any]] = None,
        complete: Optional[Callable[[str, str], str]] = None, retrieve_fn: Optional[Callable[..., Dict[str, Any]]] = None,
        sim_fn: Optional[Callable[[List[str]], Any]] = None, save: bool = True) -> Dict[str, Any]:
    """
    Answers one question from the library. Returns {'id', 'question', 'answer', 'cited', 'sources', 'mode', 'model',
    'seconds', 'checked': {'dropped', 'moved'}, 'filters'}; sources are numbered like the [n] marks in the answer.
    """
    import llm
    import retrieval
    import storage

    question = (question or "").strip()
    if not question:
        raise ValueError("Type a question")
    settings = settings or llm.load_settings()
    filters = filters or {}
    started = time.time()
    with _lock:
        found = (retrieve_fn or retrieval.retrieve)(question, k=TOP, filters=filters)
        pieces = found["pieces"]
        result: Dict[str, Any] = {"question": question, "mode": found["mode"], "filters": filters,
                                  "model": llm.describe(settings, "ask"), "checked": {"dropped": 0, "moved": 0}}
        if not pieces:
            result.update(answer=NOTHING, cited=[])
        else:
            complete = complete or llm.make_completer(settings, "ask")
            budget = max(3000, llm.context_chars(settings, "ask") - len(SYSTEM) - 1500)
            prompt = (f"Question: {question}\n\nExcerpts:\n{excerpts(pieces, budget)}\n\n"
                      f"Write the answer in {settings.get('language') or 'English'}. Cite excerpts as [n].")
            reply = complete(SYSTEM, prompt)
            try:
                data = llm.parse_json(reply)
                text = data.get("answer") if isinstance(data, dict) else None
                if isinstance(text, list):
                    text = "\n".join(f"- {x}" for x in text)
                text = str(text) if text else reply
            except llm.LLMError:
                text = reply
            if sim_fn is None:
                try:
                    sim_fn = _embedding_similarity(settings, pieces)
                except Exception:
                    sim_fn = None
            fixed = check_citations(text, len(pieces), sim_fn)
            result.update(answer=fixed["answer"], cited=fixed["cited"],
                          checked={"dropped": fixed["dropped"], "moved": fixed["moved"]})
        result["sources"] = [{"n": n, "video_id": p["video_id"], "title": p.get("title", ""), "kind": p["kind"],
                              "t": p.get("t"), "text": p["text"][:SOURCE_CHARS], "cited": n in result["cited"]}
                             for n, p in enumerate(pieces, 1)]
        result["seconds"] = round(time.time() - started, 1)
        if save:
            result["id"] = storage.save_ask({**result, "citations": result["cited"]})
    return result


def from_history(item: Dict[str, Any]) -> Dict[str, Any]:
    """A saved answer in the same shape ask() returns."""
    return {"id": item["id"], "question": item["question"], "answer": item["answer"], "cited": item["citations"],
            "sources": item["sources"], "mode": item["mode"], "model": item["model"], "seconds": item["seconds"],
            "filters": item["filters"], "created_at": item["created_at"], "checked": None}

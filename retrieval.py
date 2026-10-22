"""
Hybrid retrieval for questions (Phase 2): meaning (embedding cosine) fused with keywords (FTS5, any
term) by reciprocal-rank fusion, keyword list at half weight (spike: MRR 0.872 vs 0.63 keyword-only;
finds Hindi questions in English videos). Falls back to keywords alone when there are no embeddings.
"""
import re
import threading
from typing import Any, Callable, Dict, Iterable, List, Optional

KEYWORD_WEIGHT = 0.5
RRF_K = 60
POOL = 60            # candidates taken from each list before fusing
PER_VIDEO = 4        # at most this many pieces from one video (unless filtered to that video) ...
KEEP_TOP = 6         # ... after the best few, which are always kept, so one clearly relevant video isn't cut short
_STOP = set("the a an and or of to in on for with by is are be how what why when where do does did can could should "
            "would i my me we our you your this that these those it its from into about only own every like any which "
            "their as at before after than then there here who whom whose not no yes also just more most some".split())

_cache_lock = threading.Lock()
_cache: Dict[str, Any] = {"key": None}


def _matrix(model: str):
    """(rows, unit vectors as a float32 matrix) for the model, rebuilt only when the index changes."""
    import numpy as np
    import storage
    key = (model, storage.index_version())
    with _cache_lock:
        if _cache["key"] != key:
            rows, blobs = storage.embedded_docs(model)
            mat = np.vstack([np.frombuffer(b, dtype=np.float32) for b in blobs]) if blobs else np.zeros((0, 1), np.float32)
            _cache.update(key=key, rows=rows, mat=mat)
        return _cache["rows"], _cache["mat"]


def keyword_query(question: str) -> Optional[str]:
    """Any-term FTS5 query from the question's content words (3+ characters for the trigram index)."""
    terms = [t for t in re.findall(r"\w+", (question or "").lower()) if len(t) >= 3 and t not in _STOP]
    terms = list(dict.fromkeys(terms))[:20]
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in terms) or None


def _allowed(video: Dict[str, Any], filters: Dict[str, Any]) -> bool:
    if filters.get("video_ids") and video["video_id"] not in filters["video_ids"]:
        return False
    if filters.get("categories") and video["category"] not in filters["categories"]:
        return False
    if filters.get("playlists") and not (set(video["playlists"]) & set(filters["playlists"])):
        return False
    if not filters.get("include_done", True) and video["status"] in ("completed", "skipped"):
        return False
    return True


def _videos() -> Dict[str, Dict[str, Any]]:
    import storage
    with storage.get_connection() as conn:
        rows = conn.execute("SELECT video_id, title, channel, category, status FROM videos").fetchall()
        lists = {}
        for vid, pid in conn.execute("SELECT video_id, playlist_id FROM playlist_items WHERE removed = 0"):
            lists.setdefault(vid, []).append(pid)
    return {r["video_id"]: {**dict(r), "playlists": lists.get(r["video_id"], [])} for r in rows}


def rrf(lists: List[List[int]], weights: List[float], k: int = RRF_K) -> Dict[int, float]:
    score: Dict[int, float] = {}
    for lst, w in zip(lists, weights):
        for rank, doc in enumerate(lst):
            score[doc] = score.get(doc, 0.0) + w / (k + rank + 1)
    return score


def retrieve(question: str, k: int = 8, filters: Optional[Dict[str, Any]] = None,
             embed_query: Optional[Callable[[str], List[float]]] = None, model: Optional[str] = None) -> Dict[str, Any]:
    """
    The k best pieces for a question: {'mode': 'hybrid'|'keyword', 'pieces': [{'id', 'video_id', 'title', 'kind',
    't', 'text', 'score'}]}. filters: video_ids, categories, playlists, include_done (default True).
    """
    import numpy as np
    import embedder
    import llm
    import storage

    filters = filters or {}
    videos = _videos()
    ok = {vid for vid, v in videos.items() if _allowed(v, filters)}

    # Keywords
    keyword: List[int] = []
    q = keyword_query(question)
    if q:
        with storage.get_connection() as conn:
            try:
                for (doc_id, vid) in conn.execute(
                        "SELECT d.id, d.video_id FROM search_index JOIN search_docs d ON d.id = search_index.rowid "
                        "WHERE search_index MATCH ? ORDER BY bm25(search_index) LIMIT ?", (q, POOL * 4)):
                    if vid in ok:
                        keyword.append(doc_id)
            except Exception:
                keyword = []
    keyword = keyword[:POOL]

    # Meaning
    settings = llm.load_settings()
    model = model or embedder.model_name(settings)
    meaning: List[int] = []
    rows, mat = _matrix(model)
    if len(rows):
        if embed_query is None:
            base = settings["providers"]["ollama"].get("base_url")
            embed_query = lambda text: embedder.embed([embedder.QUERY_PREFIX + text], model, base)[0]
        try:
            qv = np.asarray(embed_query(question), dtype=np.float32)
            qv /= np.linalg.norm(qv) + 1e-12
            sims = mat @ qv
            for i in np.argsort(-sims):
                if rows[i]["video_id"] in ok:
                    meaning.append(rows[i]["id"])
                    if len(meaning) >= POOL:
                        break
        except Exception:
            meaning = []   # Ollama down: keyword results still answer
    mode = "hybrid" if meaning else "keyword"
    scores = rrf([meaning, keyword], [1.0, KEYWORD_WEIGHT])

    ranked = sorted(scores, key=lambda d: -scores[d])
    docs = _docs_by_id(ranked[:POOL * 2])
    per_video: Dict[str, int] = {}
    cap = k if filters.get("video_ids") and len(filters["video_ids"]) == 1 else PER_VIDEO
    pieces = []
    for doc_id in ranked:
        d = docs.get(doc_id)
        if not d or (per_video.get(d["video_id"], 0) >= cap and len(pieces) >= KEEP_TOP):
            continue
        per_video[d["video_id"]] = per_video.get(d["video_id"], 0) + 1
        pieces.append({**d, "title": videos.get(d["video_id"], {}).get("title", ""), "score": round(scores[doc_id], 5)})
        if len(pieces) >= k:
            break
    return {"mode": mode, "pieces": pieces}


def _docs_by_id(ids: Iterable[int]) -> Dict[int, Dict[str, Any]]:
    import json
    import storage
    ids = list(ids)
    if not ids:
        return {}
    with storage.get_connection() as conn:
        return {r["id"]: dict(r) for r in conn.execute(
            "SELECT id, video_id, kind, t, text FROM search_docs WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(ids),))}

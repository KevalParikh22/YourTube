"""
Phase 2 spike: does meaning-based retrieval (local embeddings) beat keywords on this library?

Embeds every search piece (search_docs) with a local Ollama embedding model, then answers 18
hand-written questions (paraphrased so they share few words with the answer, 2 in Hindi) with:
keyword (FTS5, any term), meaning (cosine), and hybrid (reciprocal-rank fusion).
A question counts as found when a top-k piece is in the right video within the expected time window.
"""
import json
import os
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "yourtube"))

import numpy as np

from common import save_result
import storage

MODEL = sys.argv[1] if len(sys.argv) > 1 else "qwen3-embedding:0.6b"
K = 5
QUERY_PREFIX = "Instruct: Given a question, retrieve video transcript passages that answer it\nQuery: "

# (question, video, window start s, window end s)
QUESTIONS = [
    ("How can I adapt a big model cheaply by training only a small set of extra weights?", "Wx1oiBCmxjY", 254, 330),
    ("When is it worth updating every parameter of the network?", "Wx1oiBCmxjY", 330, 356),
    ("Training with rewards that can be checked automatically, like math answers", "Wx1oiBCmxjY", 356, 463),
    ("Can I customise closed commercial models or only downloadable ones?", "Wx1oiBCmxjY", 172, 254),
    ("What does a model learn from huge internet text before instruction tuning?", "Wx1oiBCmxjY", 51, 172),
    ("A decision guide for picking how to customise an LLM", "Wx1oiBCmxjY", 463, 545),
    ("How do I run this terminal coding assistant fully offline on my own computer?", "y3d8PsWbkTk", 767, 875),
    ("Letting the coding agent change files by itself and correct its own mistakes", "y3d8PsWbkTk", 665, 767),
    ("Exploring a codebase read-only before allowing any edits", "y3d8PsWbkTk", 494, 665),
    ("Using a hosted model router service with a free tier from the terminal tool", "y3d8PsWbkTk", 282, 494),
    ("Keyboard shortcuts and slash commands inside the terminal interface", "y3d8PsWbkTk", 875, 952),
    ("Why does plain vector search struggle with questions that need connecting several facts?", "iNpc5_8irgo", 284, 731),
    ("Running the graph database locally in a container", "iNpc5_8irgo", 3662, 4333),
    ("Representing entities and their relationships as nodes and edges", "iNpc5_8irgo", 1202, 1824),
    ("An agent deciding which retrieval tool to call", "iNpc5_8irgo", 3139, 3662),
    ("Wiring up the API backend and user interface for the project", "iNpc5_8irgo", 4333, 7580),
    ("कम पैरामीटर ट्रेन करके बड़े मॉडल को सस्ते में कैसे फाइन-ट्यून करें?", "Wx1oiBCmxjY", 254, 330),
    ("ग्राफ डेटाबेस को अपने कंप्यूटर पर कैसे चलाएं?", "iNpc5_8irgo", 3662, 4333),
]
STOP = set("the a an and or of to in on for with by is are be how what why when do does can i my me this that it from "
           "into about only own every like any which their its as at before after".split())


def embed(texts, model=MODEL, batch=32):
    out = []
    for i in range(0, len(texts), batch):
        req = urllib.request.Request("http://localhost:11434/api/embed", method="POST",
                                     data=json.dumps({"model": model, "input": texts[i:i + batch], "keep_alive": "5m"}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as r:
            out += json.load(r)["embeddings"]
    v = np.array(out, dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def keyword_ranks(conn, q):
    terms = [t for t in re.findall(r"\w+", q.lower()) if len(t) >= 3 and t not in STOP]
    if not terms:
        return []
    query = " OR ".join('"' + t + '"' for t in terms)
    try:
        return [r[0] for r in conn.execute("SELECT rowid FROM search_index WHERE search_index MATCH ? ORDER BY bm25(search_index) LIMIT 50", (query,))]
    except Exception:
        return []


def rrf(*lists, k=60, weights=None):
    score = {}
    for lst, w in zip(lists, weights or [1] * len(lists)):
        for rank, doc in enumerate(lst):
            score[doc] = score.get(doc, 0) + w / (k + rank + 1)
    return sorted(score, key=lambda d: -score[d])


def main():
    conn = storage.get_connection()
    docs = [dict(r) for r in conn.execute("SELECT id, video_id, kind, t, text FROM search_docs")]
    by_id = {d["id"]: d for d in docs}
    t = time.time()
    vecs = embed([f"{d['kind']}: {d['text']}"[:2000] for d in docs])
    embed_s = time.time() - t
    ids = [d["id"] for d in docs]
    print(f"embedded {len(docs)} pieces in {embed_s:.1f}s ({len(docs) / embed_s:.1f}/s), dim {vecs.shape[1]}, "
          f"{vecs.nbytes / 1e6:.2f} MB float32")

    def hit(doc_ids, vid, a, b):
        for rank, d in enumerate(doc_ids[:K], 1):
            p = by_id[d]
            if p["video_id"] == vid and (p["t"] is None or a - 30 <= p["t"] <= b + 30):
                return rank
        return None

    VARIANTS = {"hybrid": 1.0, "hybrid_kw0.5": 0.5, "hybrid_kw0.3": 0.3}
    results = {"keyword": [], "meaning": [], **{name: [] for name in VARIANTS}}
    t = time.time()
    qv = embed([QUERY_PREFIX + q for q, *_ in QUESTIONS])
    query_s = (time.time() - t) / len(QUESTIONS)
    rows = []
    for (q, vid, a, b), v in zip(QUESTIONS, qv):
        kw = keyword_ranks(conn, q)
        sims = vecs @ v
        mean = [ids[i] for i in np.argsort(-sims)[:50]]
        r = {"keyword": hit(kw, vid, a, b), "meaning": hit(mean, vid, a, b)}
        for name, w in VARIANTS.items():
            r[name] = hit(rrf(mean, kw, weights=[1.0, w]), vid, a, b)
        for k2 in results:
            results[k2].append(r[k2])
        rows.append({"q": q[:70], **r})
        print(" ".join(f"{str(r[k2]):>5}" for k2 in results), "", q[:70])
    summary = {k2: {"found_at_5": sum(x is not None for x in v), "of": len(v),
                    "mrr": round(sum(1 / x for x in v if x) / len(v), 3)} for k2, v in results.items()}
    print(json.dumps(summary, indent=1), f"query embed {query_s * 1000:.0f} ms each")
    save_result("embed_eval", {"model": MODEL, "pieces": len(docs), "embed_s": round(embed_s, 1), "dim": int(vecs.shape[1]),
                               "query_ms": round(query_s * 1000), "summary": summary, "rows": rows})


if __name__ == "__main__":
    main()

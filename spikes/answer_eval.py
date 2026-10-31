"""
Phase 2 spike, part 2: can the local answer model cite correctly?
For a few questions: hybrid retrieval (meaning + keyword at half weight), top 8 pieces sent to the
model numbered [1]..[8]; the answer must cite [n]. Each citation is checked against the expected
video and time window, and invalid numbers are counted. Also measures answer time.
"""
import json
import re
import sys
import time
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "yourtube"))

import numpy as np

from common import save_result
from embed_eval import QUERY_PREFIX, QUESTIONS, embed, keyword_ranks, rrf
import llm
import storage

TOP = 8
PICK = [0, 6, 11, 12, 15, 16]   # LoRA, offline TUI, vector-search limits, Neo4j container, API/UI, Hindi LoRA

SYSTEM = ("You answer questions using only the numbered excerpts from the user's YouTube videos. Cite every claim with "
          "the excerpt numbers in square brackets, like [2] or [1][4]. If the excerpts don't answer the question, say so. "
          "Reply as JSON: {\"answer\": \"...\"}")


def clock(sec):
    sec = int(sec or 0)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def main():
    conn = storage.get_connection()
    docs = [dict(r) for r in conn.execute("SELECT id, video_id, kind, t, text FROM search_docs")]
    titles = dict(conn.execute("SELECT video_id, title FROM videos").fetchall())
    ids = [d["id"] for d in docs]
    by_id = {d["id"]: d for d in docs}
    vecs = embed([f"{d['kind']}: {d['text']}"[:2000] for d in docs])
    complete = llm.make_completer(llm.load_settings(), "rate")
    rows = []
    for qi in PICK:
        q, vid, a, b = QUESTIONS[qi]
        qv = embed([QUERY_PREFIX + q])[0]
        mean = [ids[i] for i in np.argsort(-(vecs @ qv))[:50]]
        top = rrf(mean, keyword_ranks(conn, q), weights=[1.0, 0.5])[:TOP]
        excerpts = "\n\n".join(f"[{n}] {titles.get(by_id[d]['video_id'], '')} @ {clock(by_id[d]['t'])} ({by_id[d]['kind']}): "
                               f"{by_id[d]['text'][:900]}" for n, d in enumerate(top, 1))
        t = time.time()
        reply = complete(SYSTEM, f"Question: {q}\n\nExcerpts:\n{excerpts}\n\nAnswer in English, in 2-5 sentences, citing excerpts.")
        took = time.time() - t
        try:
            answer = llm.parse_json(reply).get("answer", "")
        except Exception:
            answer = reply
        cited = [int(n) for n in re.findall(r"\[(\d+)\]", answer)]
        valid = [n for n in cited if 1 <= n <= len(top)]
        correct = [n for n in valid if by_id[top[n - 1]]["video_id"] == vid
                   and (by_id[top[n - 1]]["t"] is None or a - 30 <= by_id[top[n - 1]]["t"] <= b + 30)]
        row = {"q": q[:60], "seconds": round(took, 1), "citations": len(cited), "invalid": len(cited) - len(valid),
               "in_window": len(correct), "answer": answer[:400]}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
    save_result("answer_eval", rows)


if __name__ == "__main__":
    main()

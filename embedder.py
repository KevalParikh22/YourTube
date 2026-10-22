"""
Embeddings for meaning-based retrieval (Phase 2): a local Ollama embedding model turns every
search piece (storage.search_docs) into a vector. Vectors are cached by model + text hash, so only
new or changed text is ever embedded. Spike: qwen3-embedding:0.6b, ~12 pieces/s, 12 ms per query.
"""
import json
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

DEFAULT_MODEL = "qwen3-embedding:0.6b"
BATCH = 32
MAX_CHARS = 2000   # longer pieces are cut; transcript blocks are ~30 s anyway
# qwen3-embedding is instruction-aware: queries get a task prefix, documents don't
QUERY_PREFIX = "Instruct: Given a question, retrieve video transcript passages that answer it\nQuery: "


def model_name(settings: Dict[str, Any]) -> str:
    return settings.get("embed_model") or DEFAULT_MODEL


def doc_text(kind: str, text: str) -> str:
    return f"{kind}: {text}"[:MAX_CHARS]


def embed(texts: List[str], model: str, base_url: str) -> List[List[float]]:
    """Vectors from Ollama's /api/embed (batched)."""
    import llm
    out: List[List[float]] = []
    for i in range(0, len(texts), BATCH):
        req = urllib.request.Request(llm.ollama_base(base_url) + "/api/embed", method="POST",
                                     headers={"Content-Type": "application/json"},
                                     data=json.dumps({"model": model, "input": texts[i:i + BATCH], "keep_alive": "5m"}).encode())
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Ollama could not embed with {model}: {e.read().decode('utf-8', 'replace')[:200]}") from None
        out += data.get("embeddings") or []
    if len(out) != len(texts):
        raise RuntimeError(f"Ollama returned {len(out)} vectors for {len(texts)} texts")
    return out


def available(settings: Dict[str, Any]) -> bool:
    """True when the embedding model is downloaded in a running Ollama."""
    import llm
    try:
        return model_name(settings) in llm.list_models("ollama", settings)
    except Exception:
        return False


def run_embed(
    should_stop: Callable[[], bool] = lambda: False,
    embed_fn: Optional[Callable[[List[str]], List[List[float]]]] = None,
    quiet: bool = False,
) -> bool:
    """Embeds every search piece that has no vector for the current model yet. Prints progress for the job log."""
    import numpy as np
    import llm
    import storage

    settings = llm.load_settings()
    model = model_name(settings)
    if embed_fn is None:
        if not available(settings):
            if not quiet:
                print(f"Meaning-based search is off: the embedding model {model} isn't in Ollama "
                      f"(download it on the AI page or: ollama pull {model}). Keyword search still works.")
            return False
        base = settings["providers"]["ollama"].get("base_url")
        embed_fn = lambda texts: embed(texts, model, base)
    pruned = storage.prune_embeddings(model)
    todo = storage.docs_missing_embeddings(model)
    if not todo:
        if not quiet:
            print(f"Meaning index is up to date ({model}).")
        return True
    print(f"Updating the meaning index: {len(todo)} new or changed pieces with {model}"
          + (f" (removed {pruned} stale)" if pruned else "") + f". About {max(1, round(len(todo) / 12 / 60))} min.")
    done = 0
    for i in range(0, len(todo), BATCH):
        if should_stop():
            print("Stopped. The meaning index is partly updated; it continues next time.")
            return False
        chunk = todo[i:i + BATCH]
        vecs = np.asarray(embed_fn([doc_text(d["kind"], d["text"]) for d in chunk]), dtype=np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-12   # unit length: cosine = dot product
        storage.save_embeddings(model, [(d["hash"], v.tobytes(), v.shape[0]) for d, v in zip(chunk, vecs)])
        done += len(chunk)
        if len(todo) > BATCH * 4 and (i // BATCH) % 10 == 0:
            print(f"   {done}/{len(todo)}")
    print(f"Meaning index updated ({done} pieces).")
    return True

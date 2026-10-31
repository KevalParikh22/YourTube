"""
Embeddings for meaning-based retrieval (Phase 2): turns every search piece (storage.search_docs)
into a vector. Vectors are cached by model + text hash, so only new or changed text is ever embedded.
Supports local (Ollama) and cloud providers (Gemini batchEmbedContents, OpenAI /embeddings).
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


def _normalize(vecs: List[List[float]]) -> List[List[float]]:
    import numpy as np
    if not vecs:
        return []
    arr = np.asarray(vecs, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-12
    return (arr / norms).tolist()


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


class Embedder:
    model: str
    provider: str
    batch_size: int

    def docs(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError

    def queries(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError


class LocalEmbedder(Embedder):
    batch_size: int = BATCH
    provider: str = "local"

    def __init__(self, model: str, base_url: str):
        self.model = model
        self.base_url = base_url

    def docs(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        return _normalize(embed(texts, self.model, self.base_url))

    def queries(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        return _normalize(embed([QUERY_PREFIX + t for t in texts], self.model, self.base_url))


class GeminiEmbedder(Embedder):
    batch_size: int = 100
    provider: str = "gemini"

    def __init__(self, model: str, raw_model: str, key: str):
        self.model = model
        self.raw_model = raw_model
        self.key = key

    def _batch_embed(self, texts: List[str], task_type: str) -> List[List[float]]:
        if not texts:
            return []
        out: List[List[float]] = []
        raw = self.raw_model.removeprefix("models/")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{raw}:batchEmbedContents"
        for i in range(0, len(texts), self.batch_size):
            chunk = texts[i:i + self.batch_size]
            req_body = {
                "requests": [
                    {
                        "model": f"models/{raw}",
                        "content": {"parts": [{"text": t}]},
                        "taskType": task_type,
                    }
                    for t in chunk
                ]
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(req_body).encode("utf-8"),
                headers={"Content-Type": "application/json", "x-goog-api-key": self.key},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = json.load(r)
            except urllib.error.HTTPError as e:
                raise RuntimeError(f"Gemini could not embed with {self.raw_model}: {e.read().decode('utf-8', 'replace')[:200]}") from None
            except urllib.error.URLError as e:
                raise RuntimeError(f"Gemini could not embed with {self.raw_model}: {e.reason}") from None
            except TimeoutError:
                raise RuntimeError(f"Timed out waiting for Gemini embeddings ({self.raw_model})") from None
            embeddings = data.get("embeddings") or []
            if len(embeddings) != len(chunk):
                raise RuntimeError(f"Gemini returned {len(embeddings)} vectors for {len(chunk)} texts")
            out.extend([item["values"] for item in embeddings])
        return _normalize(out)

    def docs(self, texts: List[str]) -> List[List[float]]:
        return self._batch_embed(texts, "RETRIEVAL_DOCUMENT")

    def queries(self, texts: List[str]) -> List[List[float]]:
        return self._batch_embed(texts, "RETRIEVAL_QUERY")


class OpenAIEmbedder(Embedder):
    batch_size: int = 256
    provider: str = "openai"

    def __init__(self, model: str, raw_model: str, key: str, base_url: str):
        self.model = model
        self.raw_model = raw_model
        self.key = key
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")

    def _embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        out: List[List[float]] = []
        url = f"{self.base_url}/embeddings"
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        for i in range(0, len(texts), self.batch_size):
            chunk = texts[i:i + self.batch_size]
            req_body = {
                "model": self.raw_model,
                "input": chunk,
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(req_body).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = json.load(r)
            except urllib.error.HTTPError as e:
                raise RuntimeError(f"OpenAI could not embed with {self.raw_model}: {e.read().decode('utf-8', 'replace')[:200]}") from None
            except urllib.error.URLError as e:
                raise RuntimeError(f"OpenAI could not embed with {self.raw_model}: {e.reason}") from None
            except TimeoutError:
                raise RuntimeError(f"Timed out waiting for OpenAI embeddings ({self.raw_model})") from None
            items = sorted(data.get("data") or [], key=lambda x: x.get("index", 0))
            if len(items) != len(chunk):
                raise RuntimeError(f"OpenAI returned {len(items)} vectors for {len(chunk)} texts")
            out.extend([item["embedding"] for item in items])
        return _normalize(out)

    def docs(self, texts: List[str]) -> List[List[float]]:
        return self._embed(texts)

    def queries(self, texts: List[str]) -> List[List[float]]:
        return self._embed(texts)


def model_name(settings: Dict[str, Any]) -> str:
    import llm
    provider = llm.media_provider(settings, "embed")
    if provider == "gemini":
        m = (settings.get("providers") or {}).get("gemini", {}).get("embed_model") or llm.MEDIA_DEFAULT_MODELS.get(("embed", "gemini")) or "gemini-embedding-001"
        if m.startswith("gemini:"):
            m = m[7:]
        return f"gemini:{m}"
    if provider == "openai":
        m = (settings.get("providers") or {}).get("openai", {}).get("embed_model") or llm.MEDIA_DEFAULT_MODELS.get(("embed", "openai")) or "text-embedding-3-small"
        if m.startswith("openai:"):
            m = m[7:]
        return f"openai:{m}"
    return settings.get("embed_model") or DEFAULT_MODEL


def embedder_for(settings: Optional[Dict[str, Any]] = None) -> Embedder:
    import llm
    settings = settings or llm.load_settings()
    provider = llm.media_provider(settings, "embed")
    model = model_name(settings)
    if provider == "gemini":
        cfg = (settings.get("providers") or {}).get("gemini", {})
        key = llm._api_key("gemini", cfg)
        raw = model[7:] if model.startswith("gemini:") else model
        return GeminiEmbedder(model=model, raw_model=raw, key=key)
    if provider == "openai":
        cfg = (settings.get("providers") or {}).get("openai", {})
        key = llm._api_key("openai", cfg)
        base = cfg.get("base_url") or "https://api.openai.com/v1"
        raw = model[7:] if model.startswith("openai:") else model
        return OpenAIEmbedder(model=model, raw_model=raw, key=key, base_url=base)
    base = (settings.get("providers") or {}).get("ollama", {}).get("base_url") or ""
    return LocalEmbedder(model=model, base_url=base)


def available(settings: Dict[str, Any]) -> bool:
    """True when the embedding provider/model is ready to use."""
    import llm
    provider = llm.media_provider(settings, "embed")
    if provider in ("gemini", "openai"):
        return True
    try:
        return model_name(settings) in llm.list_models("ollama", settings)
    except Exception:
        return False


def run_embed(
    should_stop: Callable[[], bool] = lambda: False,
    embed_fn: Optional[Callable[[List[str]], List[List[float]]]] = None,
    quiet: bool = False,
    settings: Optional[Dict[str, Any]] = None,
) -> bool:
    """Embeds every search piece that has no vector for the current model yet. Prints progress for the job log."""
    import numpy as np
    import llm
    import storage

    settings = settings or llm.load_settings()
    emb = embedder_for(settings)
    model = emb.model
    if embed_fn is None:
        if not available(settings):
            if not quiet:
                provider = llm.media_provider(settings, "embed")
                if provider == "local":
                    print(f"Meaning-based search is off: the embedding model {model} isn't in Ollama "
                          f"(download it in Settings → AI or: ollama pull {model}). Keyword search still works.")
                else:
                    print(f"Meaning-based search is off: {provider} is not configured. Keyword search still works.")
            return False
        embed_fn = emb.docs
    pruned = storage.prune_embeddings()
    todo = storage.docs_missing_embeddings(model)
    if not todo:
        if not quiet:
            print(f"Meaning index is up to date ({model}).")
        return True
    print(f"Updating the meaning index: {len(todo)} new or changed pieces with {model}"
          + (f" (removed {pruned} stale)" if pruned else "") + f". About {max(1, round(len(todo) / 12 / 60))} min.")
    done = 0
    batch_size = getattr(emb, "batch_size", BATCH) if embed_fn == emb.docs else BATCH
    for i in range(0, len(todo), batch_size):
        if should_stop():
            print("Stopped. The meaning index is partly updated; it continues next time.")
            return False
        chunk = todo[i:i + batch_size]
        vecs = np.asarray(embed_fn([doc_text(d["kind"], d["text"]) for d in chunk]), dtype=np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-12   # unit length: cosine = dot product
        storage.save_embeddings(model, [(d["hash"], v.tobytes(), v.shape[0]) for d, v in zip(chunk, vecs)])
        done += len(chunk)
        if len(todo) > batch_size * 4 and (i // batch_size) % 10 == 0:
            print(f"   {done}/{len(todo)}")
    print(f"Meaning index updated ({done} pieces).")
    return True

"""
Pluggable LLM providers for YourTube's optional AI categorisation.

Supported providers (picked in the dashboard, stored in settings.json):
  - Claude, through the official `anthropic` SDK
  - Gemini, through the REST API
  - any OpenAI-compatible endpoint (OpenRouter, Groq, Together, vLLM... for open-weight models)
  - local models through Ollama or LM Studio (both speak the OpenAI-compatible API)

Every provider is reduced to one call: complete(system, prompt) -> text.
API keys live only in settings.json (git-ignored) or environment variables and are
never returned to the browser.
"""
import os
import re
import json
import base64
import urllib.request
import urllib.error
from typing import Any, Callable, Dict, List, Optional

_DATA_DIR = os.environ.get("YTKB_DATA_DIR") or os.path.dirname(__file__)
SETTINGS_PATH = os.environ.get("YTKB_SETTINGS") or os.path.join(_DATA_DIR, "settings.json")

PROVIDERS: Dict[str, Dict[str, Any]] = {
    "anthropic": {
        "label": "Claude (Anthropic)", "kind": "anthropic", "needs_key": True, "env": "ANTHROPIC_API_KEY",
        "model": "claude-opus-5-5", "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
    },
    "gemini": {
        "label": "Gemini (Google)", "kind": "gemini", "needs_key": True, "env": "GEMINI_API_KEY",
        "model": "gemini-2.5-flash", "models": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"],
    },
    "groq": {
        "label": "Groq", "kind": "openai", "needs_key": True, "env": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile",
        "models": ["llama-3.3-70b-versatile"],
    },
    "openai": {
        "label": "OpenAI", "kind": "openai", "needs_key": True, "env": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1", "model": "gpt-5.4-mini",
        "models": ["gpt-5.4-mini"],
    },
    "openai_compat": {
        "label": "Open-weight models (OpenAI-compatible API)", "kind": "openai", "needs_key": True, "env": "OPENAI_COMPAT_API_KEY",
        "base_url": "https://openrouter.ai/api/v1", "model": "", "models": [],
    },
    "ollama": {
        # Native API (not /v1): it can set the context size, force JSON and unload models
        "label": "Local: Ollama", "kind": "ollama", "needs_key": False, "local": True,
        "base_url": "http://localhost:11434", "model": "", "models": [],
    },
    "lmstudio": {
        "label": "Local: LM Studio", "kind": "openai", "needs_key": False, "local": True,
        "base_url": "http://localhost:1234/v1", "model": "", "models": [],
    },
}

# Jobs that can each use their own provider; unset ones use the default ("provider")
TASKS = {"categorize": "Categories", "rate": "Ratings", "summarize": "Summaries", "ask": "Questions and answers", "interests": "About you"}
# Tasks that follow another task's choice when they have none of their own
TASK_FOLLOWS = {"ask": "rate", "summarize": "rate", "interests": "rate"}

MEDIA_TASKS: Dict[str, Dict[str, Any]] = {
    "transcribe": {
        "label": "Transcription",
        "providers": ["local", "groq", "openai", "gemini"],
    },
    "slides": {
        "label": "Slide reading",
        "providers": ["local", "anthropic", "gemini", "openai", "openai_compat"],
    },
    "embed": {
        "label": "Meaning search index",
        "providers": ["local", "gemini", "openai"],
    },
}

HELPER_TASKS = ("rate", "summarize", "transcribe", "slides")

LANGUAGES = [
    "English", "Hindi", "Gujarati", "Marathi", "Bengali", "Tamil", "Telugu",
    "Kannada", "Malayalam", "Punjabi", "Urdu", "Spanish", "French", "German",
    "Portuguese", "Italian", "Dutch", "Russian", "Japanese", "Korean",
    "Chinese", "Arabic", "Turkish", "Indonesian",
]

# Approximate USD per 1M tokens (input, output), for cost estimates only
PRICES = {
    "claude-opus-5-5": (4.00, 20.00), "claude-sonnet-5-5": (2.00, 10.00), "claude-haiku-5-5": (0.10, 0.50),
    "gemini-2.5-pro": (1.25, 10.00), "gemini-2.5-flash": (0.30, 2.50), "gemini-2.5-flash-lite": (0.10, 0.40),
    "gpt-5.4-mini": (0.75, 4.50),
}

MEDIA_PRICES = {
    # Transcription: USD per audio hour
    ("groq", "whisper-large-v3-turbo"): 0.04,
    ("openai", "whisper-1"): 0.36,
    ("gemini", "gemini-3.8-flash"): 0.09,
    ("gemini", "gemini-2.5-flash"): 0.09,
    # Embeddings: USD per 1M tokens
    ("gemini", "gemini-embedding-001"): 0.15,
    ("gemini", "gemini-embedding-2"): 0.20,
    ("openai", "text-embedding-3-small"): 0.02,
}

IMAGE_TOKENS = {
    "anthropic": 440,
    "gemini": 258,
    "openai": 258,
    "openai_compat": 258,
}

MEDIA_DEFAULT_MODELS = {
    ("transcribe", "groq"): "whisper-large-v3-turbo",
    ("transcribe", "openai"): "whisper-1",
    ("embed", "gemini"): "gemini-embedding-001",
    ("embed", "openai"): "text-embedding-3-small",
}

# Ollama defaults: its own default context is small and would silently cut long transcripts
OLLAMA_NUM_CTX = 8192
OLLAMA_KEEP_ALIVE = "5m"

# Claude models that accept server-side refusal fallbacks
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
TIMEOUT = 300  # local models can be slow on a large batch


class LLMError(Exception):
    """A provider call failed in a way worth showing to the user."""


# --- settings ----------------------------------------------------------------

def default_settings() -> Dict[str, Any]:
    return {
        "provider": "none",
        "auto_after_sync": False,
        "providers": {k: {"model": p["model"], "base_url": p.get("base_url", ""), "api_key": ""}
                      for k, p in PROVIDERS.items()},
        "presets": [],            # category names the AI must keep as-is
        "category_notes": {},     # name -> one-line description used in prompts
        "proposal": None,         # pending AI-proposed category set awaiting review
        "split": None,            # pending AI category split proposal awaiting review
        "transcripts": {},        # category -> fetch transcripts? (overrides the music-name default)
        "details_after_sync": False,
        "tasks": {},              # task -> provider, overriding the default for that job
        "helpers": {},            # task -> helper provider running alongside the main provider
        "interests": "",          # free text: what the user wants to learn, used by ratings
        "language": "English",    # language for summaries and rating reasons
        "local_only": False,      # block every cloud provider
        "asr_model": "auto",      # local speech-to-text: auto = large-v3-turbo on a GPU, small on the CPU
        "visuals": {},            # category -> read slides/whiteboards for its videos
        "vision_model": "",       # Ollama model that reads frames; empty = the Ollama text model
        "visual_height": 480,     # frame resolution for slide reading (720 for small text)
        "embed_model": "qwen3-embedding:0.6b",   # Ollama embedding model for meaning-based search (Phase 2)
        "use_descriptions": True, # search video descriptions and send them to the AI (they are fetched either way)
        "show_descriptions": False,
        "rating_formula": {},     # overrides of rater.DEFAULT_FORMULA (weights, interest penalty and bonus)
    }

def load_settings(path: str = SETTINGS_PATH) -> Dict[str, Any]:
    s = default_settings()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            providers = data.pop("providers", {}) or {}
            s.update(data)
            for k, cfg in providers.items():
                if k in s["providers"] and isinstance(cfg, dict):
                    s["providers"][k].update(cfg)
    # Older settings pointed Ollama at its OpenAI-compatible /v1 path
    ollama = s["providers"]["ollama"]
    ollama["base_url"] = ollama_base(ollama.get("base_url"))
    env_url = os.environ.get("YTKB_OLLAMA_URL")
    if env_url and ollama["base_url"] in ("http://localhost:11434", "http://127.0.0.1:11434"):
        ollama["base_url"] = ollama_base(env_url)
    return s

# Settings each library has its own copy of (separate libraries). Library 1 keeps them at the top of
# settings.json, as before libraries existed; library N keeps them under settings["libraries"]["N"].
LIBRARY_KEYS = {"presets": list, "category_notes": dict, "proposal": lambda: None, "interests": str, "split": lambda: None}

def library_settings(settings: Dict[str, Any], library: Optional[int] = None) -> Dict[str, Any]:
    """{'presets', 'category_notes', 'proposal', 'interests', 'split'} for a library."""
    if not library or int(library) == 1:
        return {k: settings.get(k, make()) for k, make in LIBRARY_KEYS.items()}
    own = (settings.get("libraries") or {}).get(str(int(library))) or {}
    out = {k: own.get(k, make()) for k, make in LIBRARY_KEYS.items()}
    if not (out["interests"] or "").strip():   # a library without its own interests uses the first library's
        out["interests"] = settings.get("interests", "")
    return out

def set_library_settings(settings: Dict[str, Any], library: Optional[int], **values: Any) -> None:
    values = {k: v for k, v in values.items() if k in LIBRARY_KEYS}
    if not library or int(library) == 1:
        settings.update(values)
    else:
        settings.setdefault("libraries", {}).setdefault(str(int(library)), {}).update(values)

def for_library(settings: Dict[str, Any], library: Optional[int]) -> Dict[str, Any]:
    """A copy of the settings as seen from one library (its presets, notes, proposal and interests)."""
    return {**settings, **library_settings(settings, library)}

def ollama_base(url: Optional[str]) -> str:
    url = (url or "http://localhost:11434").rstrip("/")
    return url[:-3] if url.endswith("/v1") else url

def save_settings(settings: Dict[str, Any], path: str = SETTINGS_PATH) -> None:
    # Holds API keys: create the file owner-only from the start, not chmod it afterwards
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def _api_key(provider: str, cfg: Dict[str, Any]) -> str:
    env = PROVIDERS[provider].get("env")
    return (cfg.get("api_key") or "").strip() or (os.environ.get(env, "") if env else "")

def public_settings(settings: Dict[str, Any], library: Optional[int] = None) -> Dict[str, Any]:
    """Settings safe to send to the browser (keys are replaced by where they come from), as seen from one library."""
    settings = for_library(settings, library)
    providers = {}
    for k, p in PROVIDERS.items():
        cfg = settings["providers"].get(k, {})
        env = p.get("env")
        key_source = "saved" if cfg.get("api_key") else ("env" if env and os.environ.get(env) else None)
        providers[k] = {
            "label": p["label"], "kind": p["kind"], "needs_key": p["needs_key"], "local": p.get("local", False),
            "env": env, "models": p["models"], "model": cfg.get("model", ""),
            "base_url": cfg.get("base_url", ""), "key_source": key_source,
            "num_ctx": cfg.get("num_ctx") or OLLAMA_NUM_CTX if p["kind"] == "ollama" else None,
            "blocked": bool(settings.get("local_only")) and not p.get("local"),
        }
        providers[k]["ready"] = _provider_ready(settings, k)
    media_tasks = {
        t: {
            "label": info["label"],
            "providers": list(info["providers"]),
            "uses": describe_media(settings, t),
        }
        for t, info in MEDIA_TASKS.items()
    }
    helpers = {t: (settings.get("helpers") or {}).get(t) for t in HELPER_TASKS}
    return {
        "provider": settings["provider"], "auto_after_sync": settings["auto_after_sync"],
        "providers": providers, "presets": settings["presets"],
        "category_notes": settings["category_notes"], "proposal": settings["proposal"],
        "split": settings.get("split"),
        "tasks": {t: settings["tasks"].get(t) for t in TASKS}, "task_labels": TASKS, "task_follows": TASK_FOLLOWS,
        "media_tasks": media_tasks, "helpers": helpers,
        "interests": settings["interests"], "language": settings["language"],
        "languages": LANGUAGES,
        "local_only": bool(settings.get("local_only")),
        "asr_model": settings.get("asr_model") or "auto",
        "vision_model": settings.get("vision_model") or "",
        "visual_height": int(settings.get("visual_height") or 480),
        "embed_model": settings.get("embed_model") or "qwen3-embedding:0.6b",
        "use_descriptions": settings.get("use_descriptions", True) is not False,
        "show_descriptions": bool(settings.get("show_descriptions", False)),
        "rating_formula": settings.get("rating_formula") or {},
        "ready": is_configured(settings),
        "ready_for": {t: is_configured(settings, t) for t in TASKS},
        "uses": {t: describe(settings, t) for t in TASKS},
    }

def provider_for(settings: Dict[str, Any], task: Optional[str] = None) -> Optional[str]:
    """The provider a job uses: its own choice if set and known, else the task it follows, else the default."""
    tasks = settings.get("tasks") or {}
    chosen = tasks.get(task) if task else None
    if chosen not in PROVIDERS and task in TASK_FOLLOWS:
        chosen = tasks.get(TASK_FOLLOWS[task])
    return chosen if chosen in PROVIDERS else settings.get("provider")

def is_configured(settings: Dict[str, Any], task: Optional[str] = None) -> bool:
    return _provider_ready(settings, provider_for(settings, task))

def _provider_ready(settings: Dict[str, Any], provider: Optional[str]) -> bool:
    if provider not in PROVIDERS:
        return False
    if settings.get("local_only") and not PROVIDERS[provider].get("local"):
        return False
    cfg = settings["providers"].get(provider, {})
    if not cfg.get("model"):
        return False
    if PROVIDERS[provider]["needs_key"] and not _api_key(provider, cfg):
        return False
    if PROVIDERS[provider]["kind"] in ("openai", "ollama") and not cfg.get("base_url"):
        return False
    return True

def _media_ready(settings: Dict[str, Any], provider: str) -> bool:
    if provider == "local":
        return True
    if provider not in PROVIDERS:
        return False
    if settings.get("local_only") and not PROVIDERS[provider].get("local"):
        return False
    cfg = (settings.get("providers") or {}).get(provider, {})
    if PROVIDERS[provider]["needs_key"] and not _api_key(provider, cfg):
        return False
    base = cfg.get("base_url") or PROVIDERS[provider].get("base_url")
    if PROVIDERS[provider]["kind"] in ("openai", "ollama") and not base:
        return False
    return True

def media_provider(settings: Dict[str, Any], task: str) -> str:
    """The provider a media task uses ('local' or an allowed cloud provider).

    Falls back to 'local' when unconfigured, not allowed, or blocked by local_only.
    """
    if task not in MEDIA_TASKS:
        return "local"
    chosen = (settings.get("tasks") or {}).get(task) or "local"
    if chosen not in MEDIA_TASKS[task]["providers"]:
        return "local"
    if chosen == "local":
        return "local"
    if settings.get("local_only"):
        return "local"
    if not _media_ready(settings, chosen):
        return "local"
    return chosen

def describe_media(settings: Dict[str, Any], task: str) -> str:
    provider = media_provider(settings, task)
    if provider == "local":
        return "This computer"
    if provider in PROVIDERS:
        label = PROVIDERS[provider]["label"]
        model = MEDIA_DEFAULT_MODELS.get((task, provider)) or (settings.get("providers") or {}).get(provider, {}).get("model", "")
        return f"{label} · {model}" if model else label
    return "This computer"

def helper_for(settings: Dict[str, Any], task: str) -> Optional[str]:
    """The helper provider for a task, or None if no valid helper is configured.

    Follows the local<->cloud rule: helper must be cloud when main is local,
    and local when main is cloud. Under local_only, returns None.
    """
    if settings.get("local_only"):
        return None
    if task not in HELPER_TASKS:
        return None
    helper = (settings.get("helpers") or {}).get(task)
    if not helper or helper == "none":
        return None

    # Main provider for the task
    if task in MEDIA_TASKS:
        main = media_provider(settings, task)
    elif task in TASKS:
        main = provider_for(settings, task)
    else:
        main = None

    if not main or main == "none" or helper == main:
        return None

    main_is_local = (main == "local") or bool(PROVIDERS.get(main, {}).get("local"))
    helper_is_local = (helper == "local") or bool(PROVIDERS.get(helper, {}).get("local"))

    # Helper must be cloud when main is local, and local when main is cloud
    if main_is_local == helper_is_local:
        return None

    # Validate helper provider for the task type
    if task in MEDIA_TASKS:
        if helper not in MEDIA_TASKS[task]["providers"]:
            return None
        if not _media_ready(settings, helper):
            return None
    else:
        if helper not in PROVIDERS:
            return None
        if not _provider_ready(settings, helper):
            return None

    return helper

def describe(settings: Dict[str, Any], task: Optional[str] = None) -> str:
    provider = provider_for(settings, task)
    if provider not in PROVIDERS:
        return "no AI provider"
    return f"{PROVIDERS[provider]['label']} · {settings['providers'][provider].get('model')}"

def model_for(settings: Dict[str, Any], task: Optional[str] = None) -> str:
    provider = provider_for(settings, task)
    return settings["providers"].get(provider, {}).get("model", "") if provider in PROVIDERS else ""

def is_local(settings: Dict[str, Any], task: Optional[str] = None) -> bool:
    if task in MEDIA_TASKS:
        p = media_provider(settings, task)
        return p == "local" or bool(PROVIDERS.get(p, {}).get("local"))
    return bool(PROVIDERS.get(provider_for(settings, task) or "", {}).get("local"))

def estimate_cost(settings: Dict[str, Any], task: str, tokens_in: int, tokens_out: int) -> Optional[float]:
    """Approximate USD for a run, 0 for local models, None when the model's price is unknown."""
    if is_local(settings, task):
        return 0.0
    price = PRICES.get(model_for(settings, task))
    if not price:
        return None
    return round(tokens_in / 1e6 * price[0] + tokens_out / 1e6 * price[1], 4)


# --- HTTP helper ---------------------------------------------------------------

def _post_json(url: str, body: Dict[str, Any], headers: Dict[str, str], timeout: int = TIMEOUT) -> Dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    return _open(req, timeout)

def _get_json(url: str, headers: Dict[str, str], timeout: int = 15) -> Dict[str, Any]:
    return _open(urllib.request.Request(url, headers=headers), timeout)

def _open(req: urllib.request.Request, timeout: int) -> Dict[str, Any]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise LLMError(f"HTTP {e.code} from {req.full_url.split('?')[0]}: {detail}") from None
    except urllib.error.URLError as e:
        raise LLMError(f"Could not reach {req.full_url.split('?')[0]}: {e.reason}") from None
    except TimeoutError:
        raise LLMError(f"Timed out waiting for {req.full_url.split('?')[0]}") from None


# --- providers ---------------------------------------------------------------

def _anthropic(cfg: Dict[str, Any], key: str, system: str, prompt: str) -> str:
    try:
        import anthropic
    except ImportError:
        raise LLMError("The Claude provider needs the anthropic package. Run: bash scripts/setup.sh") from None
    client = anthropic.Anthropic(api_key=key, timeout=TIMEOUT)
    model = cfg["model"]
    kwargs: Dict[str, Any] = dict(
        model=model, max_tokens=16000, system=system,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": "low"},  # short, well-specified classification
    )
    if model in _FALLBACK_MODELS:
        kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    try:
        if "betas" in kwargs:
            response = client.beta.messages.create(**kwargs)
        else:
            response = client.messages.create(**kwargs)
    except anthropic.AuthenticationError:
        raise LLMError("Claude rejected the API key") from None
    except anthropic.NotFoundError:
        raise LLMError(f"Claude model not found: {model}") from None
    except anthropic.RateLimitError:
        raise LLMError("Claude rate limit reached. Try again in a minute.") from None
    except anthropic.APIStatusError as e:
        raise LLMError(f"Claude API error {e.status_code}: {e.message}") from None
    except anthropic.APIConnectionError:
        raise LLMError("Could not reach the Claude API. Check your connection.") from None
    if response.stop_reason == "refusal":
        raise LLMError("Claude declined this request")
    if response.stop_reason == "max_tokens":
        raise LLMError("Claude's reply was cut off (max_tokens)")
    return "".join(b.text for b in response.content if b.type == "text")

def _gemini(cfg: Dict[str, Any], key: str, system: str, prompt: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{cfg['model']}:generateContent"
    data = _post_json(url, {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }, {"x-goog-api-key": key})
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):
        reason = (data.get("promptFeedback") or {}).get("blockReason") or "empty response"
        raise LLMError(f"Gemini returned no answer ({reason})") from None
    return "".join(p.get("text", "") for p in parts)

def _openai(cfg: Dict[str, Any], key: str, system: str, prompt: str) -> str:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    data = _post_json(cfg["base_url"].rstrip("/") + "/chat/completions", {
        "model": cfg["model"], "temperature": 0,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    }, headers)
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError):
        raise LLMError(f"Unexpected response from {cfg['base_url']}") from None

def see(settings: Dict[str, Any], provider: str, image_bytes: bytes, prompt: str) -> str:
    """Ask a configured cloud vision provider to read one JPEG frame."""
    if provider not in ("anthropic", "gemini", "openai", "openai_compat"):
        raise LLMError("Unsupported vision provider")
    cfg = (settings.get("providers") or {}).get(provider, {})
    key = _api_key(provider, cfg)
    if not cfg.get("model") or not key:
        raise LLMError(f"{PROVIDERS[provider]['label']} is not configured")
    encoded = base64.b64encode(image_bytes).decode("ascii")
    kind = PROVIDERS[provider]["kind"]
    if kind == "anthropic":
        data = _post_json("https://api.anthropic.com/v1/messages", {
            "model": cfg["model"], "max_tokens": 2048, "temperature": 0,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": encoded}},
                {"type": "text", "text": prompt},
            ]}],
        }, {"x-api-key": key, "anthropic-version": "2023-06-01"})
        try:
            return "".join(x.get("text", "") for x in data["content"] if x.get("type") == "text")
        except (KeyError, TypeError):
            raise LLMError("Unexpected response from Claude") from None
    if kind == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{cfg['model']}:generateContent"
        data = _post_json(url, {"contents": [{"role": "user", "parts": [
            {"inline_data": {"mime_type": "image/jpeg", "data": encoded}}, {"text": prompt},
        ]}], "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}},
                           {"x-goog-api-key": key})
        try:
            return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, TypeError):
            raise LLMError("Gemini returned no vision answer") from None
    data = _post_json(cfg["base_url"].rstrip("/") + "/chat/completions", {
        "model": cfg["model"], "temperature": 0, "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
        ]}],
    }, {"Authorization": f"Bearer {key}"})
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise LLMError("Unexpected response from vision provider") from None

def _ollama(cfg: Dict[str, Any], key: str, system: str, prompt: str) -> str:
    body: Dict[str, Any] = {
        "model": cfg["model"], "stream": False, "format": "json",
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "options": {"temperature": 0, "num_ctx": int(cfg.get("num_ctx") or OLLAMA_NUM_CTX)},
        "keep_alive": cfg.get("keep_alive") or OLLAMA_KEEP_ALIVE,
        "think": False,  # thinking models answer faster and still follow the JSON format
    }
    url = ollama_base(cfg.get("base_url")) + "/api/chat"
    try:
        data = _post_json(url, body, {})
    except LLMError as e:
        if "think" not in str(e).lower():
            raise
        body.pop("think")  # older Ollama or a model without a thinking switch
        data = _post_json(url, body, {})
    if data.get("error"):
        raise LLMError(f"Ollama: {data['error']}")
    return (data.get("message") or {}).get("content") or ""

_CALLS = {"anthropic": _anthropic, "gemini": _gemini, "openai": _openai, "ollama": _ollama}

def context_chars(settings: Dict[str, Any], task: Optional[str] = None, remote: int = 200_000) -> int:
    """How much transcript text fits in one prompt for this task's model (about 3 chars per token)."""
    provider = provider_for(settings, task)
    if provider not in PROVIDERS or not PROVIDERS[provider].get("local"):
        return remote
    num_ctx = int(settings["providers"][provider].get("num_ctx") or OLLAMA_NUM_CTX)
    return max(4_000, (num_ctx - 2_500) * 3)  # leave room for instructions and the reply

def make_completer(settings: Dict[str, Any], task: Optional[str] = None) -> Callable[[str, str], str]:
    """Returns complete(system, prompt) -> text for the provider this task uses."""
    provider = provider_for(settings, task)
    if provider not in PROVIDERS:
        raise LLMError("Choose an AI provider first (Settings → AI in the dashboard)")
    cfg = dict(settings["providers"].get(provider, {}))
    key = _api_key(provider, cfg)
    if not cfg.get("model"):
        raise LLMError("Choose a model for the selected AI provider")
    if PROVIDERS[provider]["needs_key"] and not key:
        raise LLMError(f"Add an API key for {PROVIDERS[provider]['label']}")
    if settings.get("local_only") and not PROVIDERS[provider].get("local"):
        raise LLMError(f"Local only is on: {PROVIDERS[provider]['label']} is a cloud service. Choose a local model.")
    call = _CALLS[PROVIDERS[provider]["kind"]]
    return lambda system, prompt: call(cfg, key, system, prompt)

def list_models(provider: str, settings: Dict[str, Any]) -> List[str]:
    """Models a provider offers. Queried live for OpenAI-compatible and local servers."""
    p = PROVIDERS.get(provider)
    if not p:
        raise LLMError("Unknown provider")
    cfg = settings["providers"].get(provider, {})
    if p["kind"] == "ollama":
        data = _get_json(ollama_base(cfg.get("base_url")) + "/api/tags", {})
        return sorted(m.get("name", "") for m in data.get("models", []) if m.get("name"))
    if p["kind"] != "openai":
        return list(p["models"])
    key = _api_key(provider, cfg)
    data = _get_json(cfg.get("base_url", "").rstrip("/") + "/models",
                     {"Authorization": f"Bearer {key}"} if key else {})
    return sorted(m.get("id", "") for m in data.get("data", []) if m.get("id"))


# --- response parsing ----------------------------------------------------------

def parse_json(text: str) -> Any:
    """Pulls the first JSON object or array out of a model reply (tolerates fences and <think> blocks)."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    text = re.sub(r"```(?:json)?", "", text)
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                return decoder.raw_decode(text, i)[0]
            except json.JSONDecodeError:
                continue
    raise LLMError("The model did not return valid JSON")

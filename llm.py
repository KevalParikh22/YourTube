"""
Pluggable LLM providers for YT KB's optional AI categorisation.

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
import urllib.request
import urllib.error
from typing import Any, Callable, Dict, List, Optional

SETTINGS_PATH = os.environ.get("YTKB_SETTINGS") or os.path.join(os.path.dirname(__file__), "settings.json")

PROVIDERS: Dict[str, Dict[str, Any]] = {
    "anthropic": {
        "label": "Claude (Anthropic)", "kind": "anthropic", "needs_key": True, "env": "ANTHROPIC_API_KEY",
        "model": "claude-opus-5-5", "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
    },
    "gemini": {
        "label": "Gemini (Google)", "kind": "gemini", "needs_key": True, "env": "GEMINI_API_KEY",
        "model": "gemini-2.5-flash", "models": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"],
    },
    "openai_compat": {
        "label": "Open-weight models (OpenAI-compatible API)", "kind": "openai", "needs_key": True, "env": "OPENAI_API_KEY",
        "base_url": "https://openrouter.ai/api/v1", "model": "", "models": [],
    },
    "ollama": {
        "label": "Local: Ollama", "kind": "openai", "needs_key": False, "local": True,
        "base_url": "http://localhost:11434/v1", "model": "", "models": [],
    },
    "lmstudio": {
        "label": "Local: LM Studio", "kind": "openai", "needs_key": False, "local": True,
        "base_url": "http://localhost:1234/v1", "model": "", "models": [],
    },
}

# Jobs that can each use their own provider; unset ones use the default ("provider")
TASKS = {"categorize": "Categories", "rate": "Ratings and summaries"}

# Approximate USD per 1M tokens (input, output), for cost estimates only
PRICES = {
    "claude-opus-5-5": (4.00, 20.00), "claude-sonnet-5-5": (2.00, 10.00), "claude-haiku-5-5": (0.10, 0.50),
    "gemini-2.5-pro": (1.25, 10.00), "gemini-2.5-flash": (0.30, 2.50), "gemini-2.5-flash-lite": (0.10, 0.40),
}

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
        "transcripts": {},        # category -> fetch transcripts? (overrides the music-name default)
        "details_after_sync": False,
        "tasks": {},              # task -> provider, overriding the default for that job
        "interests": "",          # free text: what the user wants to learn, used by ratings
        "language": "English",    # language for summaries and rating reasons
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
    return s

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

def public_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Settings safe to send to the browser: keys are replaced by where they come from."""
    providers = {}
    for k, p in PROVIDERS.items():
        cfg = settings["providers"].get(k, {})
        env = p.get("env")
        key_source = "saved" if cfg.get("api_key") else ("env" if env and os.environ.get(env) else None)
        providers[k] = {
            "label": p["label"], "kind": p["kind"], "needs_key": p["needs_key"], "local": p.get("local", False),
            "env": env, "models": p["models"], "model": cfg.get("model", ""),
            "base_url": cfg.get("base_url", ""), "key_source": key_source,
        }
        providers[k]["ready"] = _provider_ready(settings, k)
    return {
        "provider": settings["provider"], "auto_after_sync": settings["auto_after_sync"],
        "providers": providers, "presets": settings["presets"],
        "category_notes": settings["category_notes"], "proposal": settings["proposal"],
        "tasks": {t: settings["tasks"].get(t) for t in TASKS}, "task_labels": TASKS,
        "interests": settings["interests"], "language": settings["language"],
        "ready": is_configured(settings),
        "ready_for": {t: is_configured(settings, t) for t in TASKS},
        "uses": {t: describe(settings, t) for t in TASKS},
    }

def provider_for(settings: Dict[str, Any], task: Optional[str] = None) -> Optional[str]:
    """The provider a job uses: its own choice if set and known, else the default."""
    chosen = (settings.get("tasks") or {}).get(task) if task else None
    return chosen if chosen in PROVIDERS else settings.get("provider")

def is_configured(settings: Dict[str, Any], task: Optional[str] = None) -> bool:
    return _provider_ready(settings, provider_for(settings, task))

def _provider_ready(settings: Dict[str, Any], provider: Optional[str]) -> bool:
    if provider not in PROVIDERS:
        return False
    cfg = settings["providers"].get(provider, {})
    if not cfg.get("model"):
        return False
    if PROVIDERS[provider]["needs_key"] and not _api_key(provider, cfg):
        return False
    if PROVIDERS[provider]["kind"] == "openai" and not cfg.get("base_url"):
        return False
    return True

def describe(settings: Dict[str, Any], task: Optional[str] = None) -> str:
    provider = provider_for(settings, task)
    if provider not in PROVIDERS:
        return "no AI provider"
    return f"{PROVIDERS[provider]['label']} · {settings['providers'][provider].get('model')}"

def model_for(settings: Dict[str, Any], task: Optional[str] = None) -> str:
    provider = provider_for(settings, task)
    return settings["providers"].get(provider, {}).get("model", "") if provider in PROVIDERS else ""

def is_local(settings: Dict[str, Any], task: Optional[str] = None) -> bool:
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
        raise LLMError("The Claude provider needs the anthropic package: pip install anthropic") from None
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

_CALLS = {"anthropic": _anthropic, "gemini": _gemini, "openai": _openai}

def make_completer(settings: Dict[str, Any], task: Optional[str] = None) -> Callable[[str, str], str]:
    """Returns complete(system, prompt) -> text for the provider this task uses."""
    provider = provider_for(settings, task)
    if provider not in PROVIDERS:
        raise LLMError("Choose an AI provider first (AI page in the dashboard)")
    cfg = dict(settings["providers"].get(provider, {}))
    key = _api_key(provider, cfg)
    if not cfg.get("model"):
        raise LLMError("Choose a model for the selected AI provider")
    if PROVIDERS[provider]["needs_key"] and not key:
        raise LLMError(f"Add an API key for {PROVIDERS[provider]['label']}")
    call = _CALLS[PROVIDERS[provider]["kind"]]
    return lambda system, prompt: call(cfg, key, system, prompt)

def list_models(provider: str, settings: Dict[str, Any]) -> List[str]:
    """Models a provider offers. Queried live for OpenAI-compatible and local servers."""
    p = PROVIDERS.get(provider)
    if not p:
        raise LLMError("Unknown provider")
    if p["kind"] != "openai":
        return list(p["models"])
    cfg = settings["providers"].get(provider, {})
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

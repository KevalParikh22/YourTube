"""
Local AI setup check (Phase 2.5, L3): what this computer has for running AI locally,
which models fit it, and pulling models into Ollama.

Everything here only reads local state (binaries, /proc, nvidia-smi, the local Ollama
server) except `pull_model`, which asks the local Ollama to download a model.
"""
import importlib.util
import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

import llm

# Recommended local models, best first, with the GPU memory (MB) each needs to run fully on the GPU.
# Qwen 3.5 small models read text and images, so one model can later also describe slides (L2).
RECOMMENDED = [
    {"model": "qwen3.5:9b", "size": "6.6 GB", "vram_mb": 7500, "why": "Best quality that fits an 8 GB GPU. Text and images."},
    {"model": "qwen3.5:4b", "size": "3.3 GB", "vram_mb": 3800, "why": "Fits a 4 GB GPU. Text and images, good at JSON and many languages."},
    {"model": "qwen3.5:2b", "size": "2.7 GB", "vram_mb": 3000, "why": "Light and fast, also usable on the CPU. Less accurate."},
]

# Embedding models for meaning-based search and questions (Phase 2), recommended first
EMBED_MODELS = [
    {"model": "qwen3-embedding:0.6b", "size": "0.64 GB",
     "why": "Recommended. Fast and multilingual: finds a Hindi question's answer in an English video (tested)."},
    {"model": "qwen3-embedding:4b", "size": "2.5 GB", "why": "Matches meaning better, about 5x slower to index. Needs ~3 GB of GPU memory."},
]

def _run(cmd: List[str], timeout: int = 5) -> Optional[str]:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None

def gpu() -> Dict[str, Any]:
    if not shutil.which("nvidia-smi"):
        return {"ok": False, "detail": "No NVIDIA GPU found. Local models run on the CPU (slower)."}
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader,nounits"])
    if not out:
        return {"ok": False, "detail": "nvidia-smi is installed but did not answer."}
    name, total, used = [x.strip() for x in out.splitlines()[0].split(",")]
    return {"ok": True, "name": name, "vram_mb": int(float(total)), "used_mb": int(float(used)),
            "detail": f"{name}, {round(int(float(total)) / 1024, 1)} GB"}

def memory() -> Dict[str, Any]:
    info = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = int(v.split()[0]) // 1024  # MB
    except OSError:
        return {"ok": False, "detail": "unknown"}
    total, avail = info.get("MemTotal", 0), info.get("MemAvailable", 0)
    return {"ok": True, "total_mb": total, "available_mb": avail,
            "detail": f"{round(total / 1024)} GB, {round(avail / 1024, 1)} GB free"}

def ffmpeg() -> Dict[str, Any]:
    out = _run(["ffmpeg", "-version"]) if shutil.which("ffmpeg") else None
    if not out:
        return {"ok": False, "detail": "Not installed. Needed later to transcribe audio and read slides."}
    m = re.search(r"ffmpeg version (\S+)", out)
    return {"ok": True, "detail": f"version {m.group(1) if m else '?'}"}

def ollama(base_url: str) -> Dict[str, Any]:
    base = llm.ollama_base(base_url)
    installed = bool(shutil.which("ollama"))
    try:
        with urllib.request.urlopen(base + "/api/version", timeout=3) as r:
            version = json.loads(r.read()).get("version")
        with urllib.request.urlopen(base + "/api/tags", timeout=5) as r:
            tags = json.loads(r.read()).get("models", [])
    except (urllib.error.URLError, OSError, ValueError):
        return {"ok": False, "installed": installed, "running": False, "models": [],
                "detail": "Installed but not running. Start it with: ollama serve" if installed else "Not installed."}
    def capabilities(name):
        try:
            req = urllib.request.Request(base + "/api/show", method="POST", data=json.dumps({"model": name}).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read()).get("capabilities") or []
        except (urllib.error.URLError, OSError, ValueError):
            return []
    models = [{"name": m.get("name"), "size_gb": round((m.get("size") or 0) / 1e9, 1),
               "vision": "vision" in capabilities(m.get("name")),
               "params": (m.get("details") or {}).get("parameter_size"),
               "quant": (m.get("details") or {}).get("quantization_level")} for m in tags]
    return {"ok": True, "installed": True, "running": True, "version": version, "models": models,
            "detail": f"version {version}, {len(models)} model{'s' if len(models) != 1 else ''} downloaded"}

def whisper() -> Dict[str, Any]:
    ok = importlib.util.find_spec("faster_whisper") is not None
    return {"ok": ok, "detail": "Installed" if ok else "Not installed yet. Comes with local transcription (Phase 2.5 L1)."}

def recommend(vram_mb: int) -> List[Dict[str, Any]]:
    """Recommended models marked with whether they fit fully on this GPU; the best fit is marked 'best'."""
    out = [{**r, "fits_gpu": vram_mb >= r["vram_mb"], "best": False} for r in RECOMMENDED]
    (next((r for r in out if r["fits_gpu"]), None) or out[-1])["best"] = True
    return out

def status() -> Dict[str, Any]:
    settings = llm.load_settings()
    g = gpu()
    o = ollama(settings["providers"]["ollama"].get("base_url"))
    return {"gpu": g, "memory": memory(), "ffmpeg": ffmpeg(), "ollama": o, "whisper": whisper(),
            "recommended": recommend(g.get("vram_mb", 0) if g["ok"] else 0), "embed": embed_status(settings, o)}


def embed_status(settings: Dict[str, Any], ollama_state: Dict[str, Any]) -> Dict[str, Any]:
    """The meaning index: which embedding model, whether Ollama has it, and how many pieces have vectors."""
    import embedder
    import storage
    model = embedder.model_name(settings)
    names = {m["name"] for m in ollama_state.get("models", [])}
    return {"model": model, "installed": model in names, "choices": [{**m, "installed": m["model"] in names} for m in EMBED_MODELS],
            **storage.embedding_status(model)}


def use_embed_model(model: str) -> None:
    """Makes this Ollama model the embedding model; the index is rebuilt for it on the next update."""
    if not re.fullmatch(r"[\w.\-/]+(:[\w.\-]+)?", model or ""):
        raise ValueError("Not a valid model name")
    settings = llm.load_settings()
    settings["embed_model"] = model
    llm.save_settings(settings)


def pull_model(model: str, should_stop: Callable[[], bool] = lambda: False) -> bool:
    """Asks the local Ollama to download a model, printing progress for the job log."""
    if not re.fullmatch(r"[\w.\-/]+(:[\w.\-]+)?", model or ""):
        print(f"Error: Not a valid model name: {model!r}")
        return False
    base = llm.ollama_base(llm.load_settings()["providers"]["ollama"].get("base_url"))
    print(f"Downloading {model} into Ollama. Large models take a while; you can keep using the app.")
    req = urllib.request.Request(base + "/api/pull", data=json.dumps({"model": model, "stream": True}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    last = -10
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            for raw in r:
                if should_stop():
                    print("Stopped. Run it again to resume the download.")
                    return False
                msg = json.loads(raw or b"{}")
                if msg.get("error"):
                    print(f"Error: {msg['error']}")
                    return False
                total, done = msg.get("total"), msg.get("completed")
                if total and done:
                    pct = int(done * 100 / total)
                    if pct >= last + 10 or pct == 100:
                        print(f"   {msg.get('status', '')[:24]:<24} {pct:>3}%  ({round(done / 1e9, 2)} of {round(total / 1e9, 2)} GB)")
                        last = pct
                elif msg.get("status") and not msg["status"].startswith("pulling"):
                    print(f"   {msg['status']}")
    except (urllib.error.URLError, OSError) as e:
        print(f"Error: Could not reach Ollama at {base}: {e}")
        return False
    print(f"{model} is ready. Choose it in Settings → AI.")
    return True


def use_model(model: str, tasks: Optional[List[str]] = None) -> None:
    """Points the Ollama provider at a model and makes it the default (and, if given, the provider for tasks)."""
    settings = llm.load_settings()
    settings["providers"]["ollama"]["model"] = model
    if settings.get("provider") in (None, "none") or not llm.is_configured(settings):
        settings["provider"] = "ollama"
    for t in tasks or []:
        settings["tasks"][t] = "ollama"
    llm.save_settings(settings)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Check and set up local AI for YourTube")
    parser.add_argument("--pull", metavar="MODEL", help="download a model into Ollama")
    parser.add_argument("--recommended", action="store_true", help="print only the best-fit model name")
    parser.add_argument("--use", metavar="MODEL", help="make this Ollama model YourTube's default AI")
    parser.add_argument("--embed-model", action="store_true", help="print only the embedding model YourTube uses")
    parser.add_argument("--use-embed", metavar="MODEL", help="make this Ollama model YourTube's embedding model")
    args = parser.parse_args()

    if args.embed_model:
        import embedder
        print(embedder.model_name(llm.load_settings()))
        return
    if args.use_embed:
        use_embed_model(args.use_embed)
        print(f"Meaning-based search now uses {args.use_embed}. Build the index: .venv/bin/python main.py --embed")
        return
    st = status()
    best = next(r for r in st["recommended"] if r["best"])
    if args.recommended:
        print(best["model"])
        return
    if args.pull:
        raise SystemExit(0 if pull_model(args.pull) else 1)
    if args.use:
        use_model(args.use)
        print(f"YourTube now uses Ollama · {args.use} by default (change it in Settings → AI).")
        return
    mark = lambda ok: "" if ok else "Warning: "
    print("YourTube local AI check")
    for key, label in (("gpu", "GPU"), ("memory", "Memory"), ("ffmpeg", "ffmpeg"), ("ollama", "Ollama"), ("whisper", "faster-whisper")):
        print(f"  {mark(st[key]['ok'])} {label:<15} {st[key]['detail']}")
    for m in st["ollama"].get("models", []):
        print(f"       · {m['name']} ({m['size_gb']} GB)")
    print(f"\nRecommended for this computer: {best['model']} ({best['size']}): {best['why']}")
    e = st["embed"]
    print(f"Meaning-based search: {e['model']} {'downloaded' if e['installed'] else 'not downloaded (ollama pull ' + e['model'] + ')'}, "
          f"{e['embedded']} of {e['pieces']} pieces indexed")


if __name__ == "__main__":
    main()

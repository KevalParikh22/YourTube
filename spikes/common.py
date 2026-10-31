"""Shared paths and helpers for the Phase 2.5 spike scripts."""
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "yourtube"))
MEDIA = os.path.join(ROOT, "media_cache", "spike")
RESULTS = os.path.join(MEDIA, "results.json")
os.makedirs(MEDIA, exist_ok=True)

# The 3 real videos in the library, chosen for variety: 18 min talk + demo, 2 h live coding, 10 min slides
VIDEOS = {
    "y3d8PsWbkTk": "DeepSeek TUI (18 min, talk + terminal demo)",
    "iNpc5_8irgo": "Graph RAG (2 h 6 min, live coding)",
    "Wx1oiBCmxjY": "LLM Fine-Tuning (10 min, slides)",
}
# Video-only clips for frame tests: (video, start s, end s)
CLIPS = [("Wx1oiBCmxjY", 0, 626), ("iNpc5_8irgo", 1200, 1800), ("y3d8PsWbkTk", 270, 570)]


def save_result(section, data):
    results = {}
    if os.path.exists(RESULTS):
        with open(RESULTS) as f:
            results = json.load(f)
    results[section] = data
    with open(RESULTS, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)


def gpu_memory_mb():
    if not shutil.which("nvidia-smi"):
        return None
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True)
    return int(out.stdout.strip().splitlines()[0]) if out.returncode == 0 else None


class GpuPeak:
    """Samples GPU memory in the background; .peak is the highest value seen (MB)."""
    def __init__(self, every=0.25):
        import threading
        self.every, self.peak, self._stop = every, gpu_memory_mb() or 0, threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            self.peak = max(self.peak, gpu_memory_mb() or 0)
            time.sleep(self.every)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join()


def unload_ollama():
    """Frees the GPU: asks Ollama to unload every loaded model (keep_alive 0)."""
    import urllib.request
    try:
        with urllib.request.urlopen("http://localhost:11434/api/ps", timeout=3) as r:
            models = [m["name"] for m in json.load(r).get("models", [])]
        for m in models:
            req = urllib.request.Request("http://localhost:11434/api/generate", method="POST",
                                         data=json.dumps({"model": m, "keep_alive": 0}).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=30).read()
        return models
    except OSError:
        return []


def preload_cuda_libs():
    """Loads the pip-installed NVIDIA cuBLAS/cuDNN so CTranslate2 finds them without LD_LIBRARY_PATH."""
    import ctypes
    import glob
    import importlib.util
    loaded = []
    for pkg in ("nvidia.cublas", "nvidia.cudnn"):
        spec = importlib.util.find_spec(pkg)
        if not spec or not spec.submodule_search_locations:
            continue
        for lib in sorted(glob.glob(os.path.join(list(spec.submodule_search_locations)[0], "lib", "lib*.so*"))):
            try:
                ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
                loaded.append(os.path.basename(lib))
            except OSError:
                pass
    return loaded


def words(text):
    import re
    return re.findall(r"[\w']+", (text or "").lower())


def agreement(a, b):
    """How alike two transcripts are, 0..1, by matching word sequences (a rough stand-in for 1 - WER)."""
    import difflib
    return round(difflib.SequenceMatcher(None, words(a), words(b), autojunk=False).ratio(), 3)

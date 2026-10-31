"""
Local transcription (Phase 2.5, L1): turn a video's audio into a timestamped transcript on this
computer with faster-whisper, for videos with no captions or captions in the wrong script.

Per video: download the ORIGINAL audio track only (auto-dubbed videos carry many; the smallest is
often a dub), anonymously; decode it to 16 kHz mono with ffmpeg; transcribe; store the transcript
next to any captions; delete the audio. One GPU model at a time: Ollama is unloaded first.
Design numbers come from the spike (docs/PRD-Phase-2.5.md §3).
"""
import glob
import platform
import sys
import json
import os
import random
import shutil
import subprocess
import threading
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.environ.get("YTKB_DATA_DIR") or ROOT
CACHE = os.path.join(_DATA_DIR, "media_cache", "asr")   # git-ignored; emptied at the start of each run

# Original track first (language_preference 10 on dubbed videos), else the smallest audio stream
AUDIO_FORMAT = "wa[language_preference>=10][acodec!=none]/wa[format_note*=original]/worstaudio[acodec!=none]/bestaudio"
MODELS = {"cuda": ("large-v3-turbo", "int8_float16"), "cpu": ("small", "int8"),
          "mlx": ("mlx-community/whisper-large-v3-turbo", "float16")}   # spike: 22-26x / 5.8x real time
SPEED = {"cuda": 20.0, "cpu": 5.0, "mlx": 15.0}   # conservative x real time, for estimates
DOWNLOAD_DELAY = 4                   # seconds between downloads, plus jitter
COOL_DOWN_C, RESUME_C = 85, 78       # pause between videos while the GPU is this hot


class BotCheck(Exception):
    """YouTube asked to confirm we are not a bot: stop and try later."""


def available() -> bool:
    import importlib.util
    return (importlib.util.find_spec("faster_whisper") is not None or
            importlib.util.find_spec("mlx_whisper") is not None) and shutil.which("ffmpeg") is not None


def preload_cuda_libs() -> int:
    """Loads pip-installed NVIDIA cuBLAS/cuDNN so CTranslate2 finds them without LD_LIBRARY_PATH."""
    import ctypes
    import importlib.util
    n = 0
    for pkg in ("nvidia.cublas", "nvidia.cudnn"):
        spec = importlib.util.find_spec(pkg)
        if not spec or not spec.submodule_search_locations:
            continue
        for lib in sorted(glob.glob(os.path.join(list(spec.submodule_search_locations)[0], "lib", "lib*.so*"))):
            try:
                ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
                n += 1
            except OSError:
                pass
    return n


def device() -> str:
    """Choose mlx on Apple Silicon, CUDA when CTranslate2 sees a GPU, else CPU."""
    import importlib.util
    if sys.platform == "darwin" and platform.machine() == "arm64":
        try:
            if importlib.util.find_spec("mlx_whisper") is not None:
                return "mlx"
        except (ImportError, ValueError):
            pass
    try:
        preload_cuda_libs()
        import ctranslate2
        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:
        return "cpu"


def choose_model(settings: Dict[str, Any], dev: str) -> (str, str):
    """(model, compute type) from settings['asr_model'] ('auto' = the spike's pick for this device)."""
    name = settings.get("asr_model") or "auto"
    default_name, compute = MODELS[dev]
    if dev == "mlx":
        return (name if name.startswith("mlx-community/") else default_name), compute
    return (default_name if name == "auto" else name), compute


PROVIDER_NAMES = {"groq": "Groq", "openai": "OpenAI", "gemini": "Gemini"}


def cloud_model(settings: Dict[str, Any], provider: str) -> str:
    """The model name for a cloud transcription provider."""
    import llm
    cfg = (settings.get("providers") or {}).get(provider) or {}
    return llm.MEDIA_DEFAULT_MODELS.get(("transcribe", provider)) or cfg.get("model") or "gemini-3.8-flash"


def estimate_cost(settings: Dict[str, Any], audio_seconds: float) -> Optional[float]:
    """Estimated USD for transcription, 0.0 for local, or None if price is unknown."""
    import llm
    engine = llm.media_provider(settings, "transcribe")
    if engine == "local":
        return 0.0
    model = cloud_model(settings, engine)
    price = llm.MEDIA_PRICES.get((engine, model))
    if price is None and engine == "gemini":
        price = 0.09
    if price is None:
        return None
    cost = max(0.0, audio_seconds) / 3600.0 * price
    return round(cost, 2)


def estimate(video_ids: Optional[List[str]] = None, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """What a transcription run would need: candidate count, audio time, device/engine and cost."""
    import llm
    import storage
    if settings is None:
        settings = llm.load_settings()
    cands = storage.videos_needing_asr(video_ids)
    engine = llm.media_provider(settings, "transcribe")
    audio = sum(v.get("duration_seconds") or 0 for v in cands)
    helper = llm.helper_for(settings, "transcribe")
    cloud = engine if engine != "local" else (helper if helper and helper != "local" else None)
    # with a cloud helper: the most it can cost (as if the cloud did every video)
    cost = estimate_cost(dict(settings, tasks={**(settings.get("tasks") or {}), "transcribe": cloud}), audio) if cloud else 0.0
    sends = (f"Audio is uploaded to {llm.PROVIDERS[cloud]['label']} for transcription"
             + (" (for the videos the helper takes; the cost shown is the most it can be)." if cloud != engine else ".")
             if cloud else "Nothing is sent; audio is processed locally.")
    if engine == "local":
        dev = device() if available() else "cpu"
        model = choose_model(settings, dev)[0]
        avail = available()
        mins = round(estimate_minutes(audio, dev) + len(cands) * 10 / 60, 1)
    else:
        dev = "cloud"
        model = cloud_model(settings, engine)
        avail = shutil.which("ffmpeg") is not None
        mins = round(len(cands) * 15 / 60, 1)
    return {
        "engine": engine,
        "available": avail,
        "device": dev,
        "model": model,
        "videos": len(cands),
        "no_captions": sum(v["reason"] == "no_captions" for v in cands),
        "wrong_script": sum(v["reason"] == "wrong_script" for v in cands),
        "audio_s": audio,
        "minutes": mins,
        "cost": cost,
        "helper": helper,
        "sends": sends,
    }


def estimate_minutes(audio_seconds: float, dev: str) -> float:
    """Rough minutes for a run: transcription at the measured speed plus ~10 s per video for downloads."""
    return audio_seconds / SPEED[dev] / 60


def gpu_temperature(drm: str = "/sys/class/drm") -> Optional[int]:
    if not shutil.which("nvidia-smi"):
        temps = []
        for device_path in glob.glob(os.path.join(drm, "card*", "device")):
            try:
                with open(os.path.join(device_path, "vendor")) as f:
                    if f.read().strip().lower() != "0x1002":
                        continue
                for sensor in glob.glob(os.path.join(device_path, "hwmon", "hwmon*", "temp1_input")):
                    try:
                        with open(sensor) as f:
                            temps.append(int(f.read().strip()) // 1000)
                    except (OSError, ValueError):
                        pass
            except OSError:
                pass
        return max(temps) if temps else None
    out = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True)
    try:
        return int(out.stdout.split()[0])
    except (IndexError, ValueError):
        return None


def unload_ollama(base_url: str) -> List[str]:
    """Frees GPU memory: tells Ollama to unload its loaded models (keep_alive 0)."""
    import llm
    base = llm.ollama_base(base_url)
    try:
        with urllib.request.urlopen(base + "/api/ps", timeout=3) as r:
            names = [m["name"] for m in json.load(r).get("models", [])]
        for name in names:
            req = urllib.request.Request(base + "/api/generate", method="POST", headers={"Content-Type": "application/json"},
                                         data=json.dumps({"model": name, "keep_alive": 0}).encode())
            urllib.request.urlopen(req, timeout=60).read()
        return names
    except (OSError, ValueError):
        return []


def download_audio(video_id: str, dest: str = CACHE) -> str:
    """Original-language audio track, smallest available, anonymously (no cookies). Returns the file path."""
    import yt_dlp
    from extractor import YT_LOCK
    from enrich import _classify_error, _js_runtimes, _QuietLogger
    os.makedirs(dest, exist_ok=True)
    opts: Dict[str, Any] = {"format": AUDIO_FORMAT, "quiet": True, "no_warnings": True, "noprogress": True,
                            "logger": _QuietLogger(), "outtmpl": os.path.join(dest, "%(id)s.%(ext)s"), "socket_timeout": 30}
    runtimes = _js_runtimes()
    if runtimes:
        opts["js_runtimes"] = runtimes
    try:
        with YT_LOCK, yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
            return y.prepare_filename(info)
    except yt_dlp.utils.DownloadError as e:
        if _classify_error(str(e)) == "bot":
            raise BotCheck(str(e)) from None
        raise


def decode(path: str):
    """16 kHz mono float32 via ffmpeg (faster-whisper's PyAV decoder breaks with newer PyAV; see the spike)."""
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", path,
                          "-f", "s16le", "-ac", "1", "-ar", "16000", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


class Transcriber:
    """Loads the Whisper model once per run and transcribes decoded audio into segments."""
    def __init__(self, name: str, dev: str, compute: str):
        from faster_whisper import WhisperModel
        self.name = name
        self.model = WhisperModel(name, device=dev, compute_type=compute, cpu_threads=max(2, (os.cpu_count() or 4) // 2))

    def __call__(self, samples, progress: Callable[[float], None] = lambda p: None) -> (List[Dict[str, Any]], str):
        total = max(1.0, len(samples) / 16000)
        segments, info = self.model.transcribe(samples, beam_size=5, vad_filter=True)
        out = []
        for seg in segments:  # the generator does the work as it is consumed
            text = seg.text.strip()
            if text:
                out.append({"start": round(seg.start, 2), "dur": round(seg.end - seg.start, 2), "text": text})
            progress(min(1.0, seg.end / total))
        return out, info.language


class MlxTranscriber:
    """Transcribe with mlx-whisper on Apple Silicon."""
    def __init__(self, name: str, dev: str, compute: str):
        import mlx_whisper
        self.name = name
        self.module = mlx_whisper

    def __call__(self, samples, progress: Callable[[float], None] = lambda p: None) -> (List[Dict[str, Any]], str):
        result = self.module.transcribe(samples, path_or_hf_repo=self.name, verbose=None)
        out = []
        for seg in result.get("segments", []):
            text = str(seg.get("text") or "").strip()
            if text:
                out.append({"start": round(seg["start"], 2), "dur": round(seg["end"] - seg["start"], 2), "text": text})
        progress(1.0)
        return out, result["language"]


def _clear_cache():
    if os.path.isdir(CACHE):
        shutil.rmtree(CACHE, ignore_errors=True)


def run_asr(
    video_ids: Optional[List[str]] = None,
    should_stop: Callable[[], bool] = lambda: False,
    download: Callable[[str], str] = download_audio,
    make_transcriber: Callable[[str, str, str], Callable] = Transcriber,
    decode_audio: Callable[[str], Any] = decode,
    sleep: Callable[[float], None] = time.sleep,
    transcribe_cloud: Optional[Callable] = None,
    settings: Optional[Dict[str, Any]] = None,
) -> bool:
    """Transcribes candidate videos (or the given ones). Prints progress for the job log."""
    import llm
    import storage
    import parallel

    if settings is None:
        settings = llm.load_settings()
    engine = llm.media_provider(settings, "transcribe")
    queue = storage.videos_needing_asr(video_ids)

    if engine == "local":
        print(f"Local transcription: {len(queue)} video{'s' if len(queue) != 1 else ''}"
              + (f" ({sum(1 for v in queue if v.get('reason') == 'no_captions')} without captions, "
                 f"{sum(1 for v in queue if v.get('reason') == 'wrong_script')} with captions in the wrong script)" if queue else "") + ".")
        if not queue:
            return True
        if not available():
            print("Error: Local transcription needs faster-whisper and ffmpeg. Run: bash scripts/setup.sh --local-ai")
            return False
        _clear_cache()
        dev = device() if make_transcriber is Transcriber else "cpu"
        name, compute = choose_model(settings, dev)
        transcriber_factory = MlxTranscriber if dev == "mlx" and make_transcriber is Transcriber else make_transcriber
        hours = sum(v.get("duration_seconds") or 0 for v in queue) / 3600
        print(f"   {name} on {'the GPU' if dev in ('cuda', 'mlx') else 'the CPU'}. About {max(1, round(estimate_minutes(hours * 3600, dev)))} min "
              f"for {hours:.1f} h of audio, plus downloads. Audio is deleted after each video.")
    else:
        hours = sum(v.get("duration_seconds") or 0 for v in queue) / 3600
        cloud_mod = cloud_model(settings, engine)
        provider_name = PROVIDER_NAMES.get(engine, engine.capitalize())
        cost = estimate_cost(settings, hours * 3600)
        cost_val = 0.0 if cost is None else cost
        print(f"{provider_name} {cloud_mod} · about ${cost_val:.2f} for {hours:.1f} h of audio. Audio is uploaded to {provider_name}, then deleted here.")
        if not queue:
            return True
        if shutil.which("ffmpeg") is None:
            print("Error: Transcription needs ffmpeg. Run: bash scripts/setup.sh")
            return False
        _clear_cache()
        print(f"   {len(queue)} video{'s' if len(queue) != 1 else ''}"
              + (f" ({sum(1 for v in queue if v.get('reason') == 'no_captions')} without captions, "
                 f"{sum(1 for v in queue if v.get('reason') == 'wrong_script')} with captions in the wrong script)" if queue else "") + ".")
        if engine == "gemini":
            print("   Timestamps from Gemini are approximate.")

    helper = llm.helper_for(settings, "transcribe")
    engines = [engine] + ([helper] if helper else [])
    for worker_engine in engines:
        if worker_engine == "local" and not available():
            print("Error: Local transcription needs faster-whisper and ffmpeg. Run: bash scripts/setup.sh --local-ai")
            return False
        if worker_engine != "local" and shutil.which("ffmpeg") is None:
            print("Error: Transcription needs ffmpeg. Run: bash scripts/setup.sh")
            return False

    for v in queue:
        storage.set_job(v["video_id"], "asr", status="queued", error=None, progress=0)

    done = failed = 0
    tally = threading.Lock()   # two workers update the counts when a helper engine shares the queue
    bot_seen = [False]
    counts = {name: 0 for name in engines}
    worker_states = {}

    def process(worker_engine, v):
        nonlocal done, failed
        state = worker_states[worker_engine]
        n = state["started"] = state["started"] + 1
        prefix = f"[{n}/{len(queue)}] "
        if helper:
            prefix = f"[{n}/{len(queue)}] [{worker_engine}] "
        vid, label = v["video_id"], (v.get("title") or v["video_id"])[:60]
        if should_stop():
            return False
        timings: Dict[str, float] = {}
        path = None
        try:
            storage.set_job(vid, "asr", status="downloading")
            t = time.time()
            path = download(vid)
            timings["download_s"] = round(time.time() - t, 1)
            last = [0.0]

            def progress(p, vid=vid):
                if p - last[0] >= 0.1:
                    last[0] = p
                    storage.set_job(vid, "asr", progress=round(p, 2))

            if worker_engine == "local":
                samples = decode_audio(path)
                timings["audio_s"] = round(len(samples) / 16000)
                if state["transcriber"] is None:
                    if state["dev"] in ("cuda", "mlx"):
                        freed = unload_ollama(settings["providers"]["ollama"].get("base_url"))
                        if freed:
                            print(f"   Unloaded {', '.join(freed)} from the GPU to make room.")
                    print(f"   Loading {state['name']} (the first time downloads it: ~1.6 GB for large-v3-turbo, ~0.5 GB for small)…")
                    state["transcriber"] = state["factory"](state["name"], state["dev"], state["compute"])
                storage.set_job(vid, "asr", status="transcribing")
                t = time.time()
                segments, language = state["transcriber"](samples, progress)
                timings["transcribe_s"] = round(time.time() - t, 1)
                save_model = state["name"]
            else:
                storage.set_job(vid, "asr", status="transcribing")
                t = time.time()
                import cloud_asr
                worker_settings = state["settings"]
                model = cloud_model(worker_settings, worker_engine)
                call_transcribe = transcribe_cloud or cloud_asr.transcribe
                segments, language = call_transcribe(path, worker_engine, worker_settings, progress=progress)
                timings["transcribe_s"] = round(time.time() - t, 1)
                timings["audio_s"] = v.get("duration_seconds") or (round(segments[-1]["start"] + segments[-1].get("dur", 0)) if segments else 0)
                save_model = f"{worker_engine}:{model}"

            storage.save_local_transcript(vid, segments, language=language, model=save_model)
            storage.set_job(vid, "asr", status="done", progress=1.0, timings=timings)
            with tally:
                done += 1
                counts[worker_engine] += 1
            audio_secs = timings.get("audio_s") or (v.get("duration_seconds") or 0)
            speed = audio_secs / max(0.1, timings["transcribe_s"])
            note = " · captions kept; choose in the video panel" if v.get("reason") == "wrong_script" else ""
            parallel.safe_print(f"   {prefix}✓ {label} · {language} · {len(segments)} lines · {speed:.0f}× real time · {save_model}{note}")
            return True
        except BotCheck:
            storage.set_job(vid, "asr", status="queued")
            bot_seen[0] = True
            return False
        except Exception as e:
            with tally:
                failed += 1
            storage.set_job(vid, "asr", status="failed", error=str(e)[:300], timings=timings)
            parallel.safe_print(f"   {prefix}Warning: {label}: {str(e)[:120]}")
            if helper:   # let parallel.share count it: a worker failing three times in a row stops, the other carries on
                raise
            return False
        finally:
            if path and os.path.exists(path):
                os.remove(path)
            if worker_engine == "local":
                _cool_down(sleep)
            if not should_stop():
                sleep(DOWNLOAD_DELAY + random.uniform(0, DOWNLOAD_DELAY / 2))

    def worker(worker_engine):
        worker_settings = dict(settings)
        worker_settings["tasks"] = dict(settings.get("tasks") or {})
        worker_settings["tasks"]["transcribe"] = worker_engine
        if worker_engine == "local":
            dev = device() if make_transcriber is Transcriber else "cpu"
            name, compute = choose_model(settings, dev)
            factory = MlxTranscriber if dev == "mlx" and make_transcriber is Transcriber else make_transcriber
        else:
            dev, name, compute, factory = "cloud", cloud_model(worker_settings, worker_engine), None, None
        worker_states[worker_engine] = {"settings": worker_settings, "dev": dev, "name": name,
                                        "compute": compute, "factory": factory, "transcriber": None, "started": 0}

        def run_video(v):
            process(worker_engine, v)
        return run_video

    try:
        if helper:
            count_map = parallel.share(queue, {str(e): worker(e) for e in engines}, should_stop=lambda: should_stop() or bot_seen[0],
                                       on_error=lambda name, item, exc: None)
        else:
            fn = worker(engine)
            for v in queue:
                if should_stop():
                    break
                try:
                    fn(v)
                    if bot_seen[0]:
                        break
                except BotCheck:
                    print("YouTube asked to confirm we're not a bot. Stopping; try again in an hour or two.")
                    return False
                except Exception:
                    pass
        if bot_seen[0]:
            print("YouTube asked to confirm we're not a bot. Stopping; try again in an hour or two.")
    finally:
        _clear_cache()   # jobs never reached stay "queued", which the UI shows as not started
    if should_stop():
        print("Stopped. Finished videos are saved; run again for the rest.")
    print(f"{done} transcribed" + (f", {failed} failed" if failed else "") + ".")
    if helper:
        print("   By engine: " + ", ".join(f"{count} by {name}" for name, count in counts.items()) + ".")
    return not bot_seen[0] and (done > 0 or failed == 0)


def _cool_down(sleep: Callable[[float], None], max_wait: float = 90) -> None:
    temp = gpu_temperature()
    if temp is None or temp < COOL_DOWN_C:
        return
    print(f"   GPU at {temp} °C: pausing to cool down…")
    waited = 0.0
    while waited < max_wait:
        sleep(5)
        waited += 5
        temp = gpu_temperature()
        if temp is None or temp <= RESUME_C:
            break

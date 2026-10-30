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
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional

ROOT = os.path.dirname(os.path.abspath(__file__))
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
    from enrich import _classify_error, _js_runtimes, _QuietLogger
    os.makedirs(dest, exist_ok=True)
    opts: Dict[str, Any] = {"format": AUDIO_FORMAT, "quiet": True, "no_warnings": True, "noprogress": True,
                            "logger": _QuietLogger(), "outtmpl": os.path.join(dest, "%(id)s.%(ext)s"), "socket_timeout": 30}
    runtimes = _js_runtimes()
    if runtimes:
        opts["js_runtimes"] = runtimes
    try:
        with yt_dlp.YoutubeDL(opts) as y:
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
) -> bool:
    """Transcribes candidate videos (or the given ones) locally. Prints progress for the job log."""
    import llm
    import storage

    settings = llm.load_settings()
    queue = storage.videos_needing_asr(video_ids)
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
    for v in queue:
        storage.set_job(v["video_id"], "asr", status="queued", error=None, progress=0)

    transcriber = None
    done = failed = 0
    try:
        for n, v in enumerate(queue, 1):
            vid, label = v["video_id"], (v.get("title") or v["video_id"])[:60]
            if should_stop():
                print("Stopped. Finished videos are saved; run again for the rest.")
                break
            timings: Dict[str, float] = {}
            path = None
            try:
                storage.set_job(vid, "asr", status="downloading")
                t = time.time()
                path = download(vid)
                timings["download_s"] = round(time.time() - t, 1)
                samples = decode_audio(path)
                timings["audio_s"] = round(len(samples) / 16000)
                if transcriber is None:
                    if dev in ("cuda", "mlx"):
                        freed = unload_ollama(settings["providers"]["ollama"].get("base_url"))
                        if freed:
                            print(f"   Unloaded {', '.join(freed)} from the GPU to make room.")
                    print(f"   Loading {name} (the first time downloads it: ~1.6 GB for large-v3-turbo, ~0.5 GB for small)…")
                    transcriber = transcriber_factory(name, dev, compute)
                storage.set_job(vid, "asr", status="transcribing")
                t = time.time()
                last = [0.0]

                def progress(p, vid=vid):
                    if p - last[0] >= 0.1:
                        last[0] = p
                        storage.set_job(vid, "asr", progress=round(p, 2))
                segments, language = transcriber(samples, progress)
                timings["transcribe_s"] = round(time.time() - t, 1)
                storage.save_local_transcript(vid, segments, language=language, model=name)
                storage.set_job(vid, "asr", status="done", progress=1.0, timings=timings)
                done += 1
                speed = timings["audio_s"] / max(0.1, timings["transcribe_s"])
                note = " · captions kept; choose in the video panel" if v.get("reason") == "wrong_script" else ""
                print(f"   [{n}/{len(queue)}] ✓ {label} · {language} · {len(segments)} lines · {speed:.0f}× real time{note}")
            except BotCheck:
                storage.set_job(vid, "asr", status="queued")
                print("YouTube asked to confirm we're not a bot. Stopping; try again in an hour or two.")
                return False
            except Exception as e:  # one bad video shouldn't stop the run
                failed += 1
                storage.set_job(vid, "asr", status="failed", error=str(e)[:300], timings=timings)
                print(f"   [{n}/{len(queue)}] Warning: {label}: {str(e)[:120]}")
            finally:
                if path and os.path.exists(path):
                    os.remove(path)   # media is temporary (Phase 2.5 rule)
            if n < len(queue):
                _cool_down(sleep)
                sleep(DOWNLOAD_DELAY + random.uniform(0, DOWNLOAD_DELAY / 2))
    finally:
        _clear_cache()   # jobs never reached stay "queued", which the UI shows as not started
    print(f"{done} transcribed" + (f", {failed} failed" if failed else "") + ".")
    return done > 0 or failed == 0


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

"""
Spike 2: local speech-to-text with faster-whisper on this machine.
For each model/device: load time, speed (x real time), peak GPU memory, detected language, and
word agreement with the video's YouTube captions over the same minutes.
Run after download.py. Ollama models are unloaded first so the GPU is free.
"""
import os
import sys
import time

from common import GpuPeak, MEDIA, VIDEOS, agreement, gpu_memory_mb, preload_cuda_libs, save_result, unload_ollama

SECONDS = 600  # transcribe the first 10 minutes of each video
ONLY_VIDEOS = []
CONFIGS = [  # (model, device, compute type)
    ("large-v3-turbo", "cuda", "int8_float16"),
    ("small", "cuda", "int8_float16"),
    ("small", "cpu", "int8"),
    ("large-v3-turbo", "cpu", "int8"),
]


def decode(path, seconds):
    """16 kHz mono float32 via ffmpeg (faster-whisper's own PyAV decoder breaks with newer PyAV releases)."""
    import subprocess
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-t", str(seconds), "-i", path,
                          "-f", "s16le", "-ac", "1", "-ar", "16000", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def caption_text(video_id, seconds):
    import storage
    return " ".join(s["text"] for s in storage.get_segments(video_id) if s["start"] < seconds)


def main(configs):
    print("unloaded from Ollama:", unload_ollama())
    print("cuda libs:", len(preload_cuda_libs()))
    from faster_whisper import WhisperModel
    videos = [v for v in VIDEOS if not ONLY_VIDEOS or v in ONLY_VIDEOS]
    audio = {vid: decode(os.path.join(MEDIA, f"{vid}.m4a"), SECONDS) for vid in videos}
    rows = []
    for name, device, compute in configs:
        if device == "cuda" and name == "large-v3-turbo":
            time.sleep(2)
        base = gpu_memory_mb()
        t = time.time()
        try:
            model = WhisperModel(name, device=device, compute_type=compute, cpu_threads=8)
        except Exception as e:
            rows.append({"model": name, "device": device, "error": str(e)[:300]})
            print(rows[-1])
            continue
        load = time.time() - t
        for vid, samples in audio.items():
            secs = len(samples) / 16000
            with GpuPeak() as peak:
                t = time.time()
                segments, info = model.transcribe(samples, beam_size=5, vad_filter=True)
                text = " ".join(s.text.strip() for s in segments)  # the generator does the work
                took = time.time() - t
            row = {"model": name, "device": device, "compute": compute, "video": vid, "audio_s": round(secs),
                   "load_s": round(load, 1), "seconds": round(took, 1), "x_realtime": round(secs / took, 1),
                   "gpu_peak_mb": peak.peak if device == "cuda" else None, "gpu_base_mb": base,
                   "language": info.language, "lang_prob": round(info.language_probability, 2),
                   "agreement_with_captions": agreement(text, caption_text(vid, secs)), "words": len(text.split()),
                   "sample": text[:240]}
            rows.append(row)
            print({k: v for k, v in row.items() if k != "sample"})
            load = 0.0
        del model
    save_result("asr" + ("_" + "_".join(ONLY_VIDEOS) if ONLY_VIDEOS else ""), rows)


if __name__ == "__main__":
    ONLY_VIDEOS = [a for a in sys.argv[1:] if a in VIDEOS]
    only = [a for a in sys.argv[1:] if a not in VIDEOS]  # e.g. "small" to run only matching configs
    main([c for c in CONFIGS if not only or any(o in "-".join(c) for o in only)])

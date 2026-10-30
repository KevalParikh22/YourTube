"""
Spike R1 (cross-platform plan): faster-whisper vs whisper.cpp (pywhispercpp) on the CPU.
Same 16 kHz mono WAV, same model size, same thread count. Reports load time, speed (x real time),
and word agreement between the two transcripts and, when a YouTube .vtt is given, with those captions.

Needs a separate venv with faster-whisper and pywhispercpp (whisper.cpp is not a YourTube dependency):
  python3 -m venv --without-pip /tmp/asrvenv
  python3 -m pip --python /tmp/asrvenv/bin/python install faster-whisper pywhispercpp numpy
  /tmp/asrvenv/bin/python spikes/asr_engines.py clip.wav [model] [threads] [language] [captions.vtt]
"""
import difflib
import re
import sys
import time
import wave

import numpy as np


def load(path):
    with wave.open(path) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1, "expects 16 kHz mono"
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def words(text):
    return re.findall(r"[a-z0-9']+", text.lower())


def vtt_words(path, seconds):
    """Words of a YouTube .vtt up to `seconds`; auto-captions repeat each line, so consecutive repeats are dropped."""
    lines, start, last = [], 0.0, None
    for line in open(path, encoding="utf-8"):
        m = re.match(r"(\d+):(\d+):([\d.]+) -->", line)
        if m:
            start = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
            continue
        text = re.sub(r"<[^>]+>", "", line).strip()
        if text and "-->" not in text and start < seconds and text != last and not text.startswith(("WEBVTT", "Kind:", "Language:")):
            lines.append(text)
            last = text
    return words(" ".join(lines))


def run_faster_whisper(samples, model, threads, language):
    from faster_whisper import WhisperModel
    t0 = time.time()
    m = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=threads)
    t1 = time.time()
    segments, info = m.transcribe(samples, beam_size=5, vad_filter=True, language=language)
    text = " ".join(s.text for s in segments)
    return t1 - t0, time.time() - t1, info.language, text


def run_whisper_cpp(samples, model, threads, language):
    from pywhispercpp.model import Model
    t0 = time.time()
    m = Model(model, n_threads=threads, print_progress=False, print_realtime=False, language=language or "auto")
    t1 = time.time()
    segments = m.transcribe(samples)
    text = " ".join(s.text for s in segments)
    return t1 - t0, time.time() - t1, language or "auto", text


def main():
    path = sys.argv[1]
    model = sys.argv[2] if len(sys.argv) > 2 else "small"
    threads = int(sys.argv[3]) if len(sys.argv) > 3 else 8
    language = (sys.argv[4] if len(sys.argv) > 4 else None) or None   # "" = detect, like YourTube
    reference = sys.argv[5] if len(sys.argv) > 5 else None
    samples = load(path)
    seconds = len(samples) / 16000
    print(f"clip: {seconds / 60:.1f} min, model {model}, {threads} threads, language {language or 'auto'}")
    texts = {}
    for name, fn in (("faster-whisper int8", run_faster_whisper), ("whisper.cpp", run_whisper_cpp)):
        load_s, run_s, lang, text = fn(samples, model, threads, language)
        texts[name] = words(text)
        print(f"{name:20} load {load_s:5.1f} s  transcribe {run_s:6.1f} s  {seconds / run_s:5.1f}x real time  "
              f"lang {lang}  {len(texts[name])} words")
    a, b = texts.values()
    print(f"word agreement between engines: {difflib.SequenceMatcher(None, a, b, autojunk=False).ratio():.2%}")
    if reference:
        ref = vtt_words(reference, seconds)
        for name, got in texts.items():
            print(f"{name:20} agreement with YouTube captions ({len(ref)} words): "
                  f"{difflib.SequenceMatcher(None, ref, got, autojunk=False).ratio():.2%}")


if __name__ == "__main__":
    main()

# R1: Local transcription on AMD, Intel and Apple (2026-10-30)

Spike for `docs/PLAN-cross-platform.md` (task R1). Question: how can local transcription use the graphics card on computers without NVIDIA, and what should run on the CPU?

## What the research found

| Engine | What it runs on | Install story | Notes |
|---|---|---|---|
| **faster-whisper on CTranslate2** (what YourTube uses) | NVIDIA (CUDA, PyPI wheels); **AMD through the ROCm build** | PyPI for NVIDIA/CPU. For AMD the ROCm wheels are **only on CTranslate2's GitHub releases**, as `rocm-python-wheels-Linux.zip` / `rocm-python-wheels-Windows.zip` (v4.8.4, 2026-10-25: 371 MB / 151 MB). | AMD support since v4.7.0 (Feb 2026). The Python API still says `device="cuda"`: CTranslate2 uses one device type for CUDA and HIP, so faster-whisper should work unchanged (no project states this outright; untested). Windows: RDNA 2/3 (RX 6000/7000), needs the ROCm 7.2 runtime (the 7.1.1 HIP SDK installer is incompatible). Linux: needs a ROCm 7.x runtime; the exact card list isn't published. Older APUs such as Vega (gfx90c) are not covered. |
| **mlx-whisper** | Apple Silicon only | `pip install mlx-whisper` (pure-Python wheel, latest 0.4.3, Aug 2025) | `mlx_whisper.transcribe(audio, path_or_hf_repo=..., verbose=None)` takes a numpy float32 array and returns `{text, segments[{start, end, text, ...}], language}`. Model `mlx-community/whisper-large-v3-turbo`. Reported 15-55x real time on M-series chips (not measured here). No progress callback. |
| **whisper.cpp** (`pywhispercpp`) | CPU everywhere; GPU (Vulkan, Metal, HIP, SYCL) only when built from source | PyPI wheels (1.5.1) are **CPU-only**. GPU builds need the Vulkan SDK / Xcode and a compiler. | One engine for every vendor in theory, but no GPU wheels to install. Separate ggml model files (small: 465 MB). |
| PyTorch + openai-whisper | ROCm (Linux), MPS (partial) | Several GB | Last resort; not pursued. |

**Ollama (the local AI models, not transcription):** official docs list AMD Radeon through ROCm v7 on Linux (RX 6800 and up, 7000, 9000 series, recent Ryzen AI APUs) and on Windows (RX 7600 and up, 7000 series only). Other AMD and **Intel** GPUs go through Vulkan, which the docs describe without a support label; Intel community reports in 2026 still show CPU fallbacks. Apple Silicon uses Metal. So the machine check's "Ollama may not use this GPU yet" for Intel stays correct.

## Measured on this machine (CPU only)

AMD Ryzen 7 7730U (8 cores / 16 threads) with Vega integrated graphics (2 GB carve-out, gfx90c: not on any ROCm list, so CPU only). Clip: the first 10 minutes of `y3d8PsWbkTk` (talk + terminal demo), 16 kHz mono. Model `small`, 8 threads, beam 5 where configurable. Script: `spikes/asr_engines.py`.

| Engine | Load | Transcribe 10 min | Speed | Words | Agreement with YouTube's English captions (1,898 words) |
|---|---|---|---|---|---|
| faster-whisper `small` int8 (YourTube's CPU path, beam 5, VAD) | 1.0 s | 158.5 s | **3.8x** real time | 1,894 | **94.2 %** |
| whisper.cpp `small` (pywhispercpp 1.5.1, defaults) | 0.3 s | 95.4 s | **6.3x** real time | 1,906 | **93.7 %** |

The two transcripts agree with each other on 96.7 % of words. Both detected English. (The earlier Phase 2.5 spike measured 5.8x for faster-whisper `small` on the CPU of a different machine.)

Lesson from a first, discarded run: a plain `worstaudio` download of this video took an **auto-dubbed Arabic** track (YouTube now adds dubbed tracks to many videos). YourTube's own `local_asr.AUDIO_FORMAT` correctly picked the English original (`139-19`), so YourTube is not affected; any new download code must keep using it.

Not tested here (no hardware): NVIDIA, ROCm on a supported Radeon, Apple Silicon (mlx-whisper), Intel GPUs.

## Recommendation

| Computer | Transcription engine | Why |
|---|---|---|
| NVIDIA | faster-whisper, CUDA (unchanged) | Works today. |
| AMD Radeon RX 6000 / 7000 / 9000 (Linux, Windows) | faster-whisper on **CTranslate2's ROCm build** | Same engine and code path (`device="cuda"`), so nothing downstream changes. Installing it is manual for now (ROCm runtime + the wheel from CTranslate2's GitHub release); automate after a real AMD test. |
| Apple Silicon | **mlx-whisper** (`mlx-community/whisper-large-v3-turbo`) | Only practical GPU path on a Mac; pip-installable; takes the same numpy audio. |
| Intel GPUs, older AMD APUs, no GPU, Docker | faster-whisper on the CPU (unchanged) | whisper.cpp is only ~1.65x faster on the CPU at equal quality, and its GPU builds need compiling. Not worth a third engine and a second model download. |

Dropped: a user-facing `asr_engine` setting. The engine follows from the hardware (mlx on Apple Silicon when installed, else faster-whisper), so there is nothing useful to choose.
Follow-up idea (not in this plan): try `beam_size=1` on the CPU path; whisper.cpp's speed lead is partly greedy decoding.

## Sources (read 2026-10-30)
- CTranslate2 releases (v4.7.0 AMD support, v4.8.4 latest) and `docs/building_rocm_windows.md`: https://github.com/OpenNMT/CTranslate2/releases
- Ollama GPU support: https://docs.ollama.com/gpu
- mlx-whisper: https://github.com/ml-explore/mlx-examples/tree/main/whisper and `mlx_whisper/transcribe.py`; model list via the Hugging Face API (`mlx-community/whisper-large-v3-turbo`)
- pywhispercpp: https://github.com/absadiki/pywhispercpp (PyPI wheels CPU-only)

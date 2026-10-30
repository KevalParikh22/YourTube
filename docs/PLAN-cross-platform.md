# Plan: Run anywhere: one restart command, optional Docker, GPUs beyond NVIDIA

**Status: planning only (2026-10-30). Do not implement until the user says so.** Kept in `docs/` (tracked) so it can be read on other systems.

**Goal:**
- Starting, restarting and keeping YourTube running works the same way on Linux, macOS and Windows.
- An optional Docker setup exists for people who prefer containers.
- Local AI uses the graphics card on **AMD, Intel and Apple** computers, not only NVIDIA.

**Branch (when started):** feat/cross-platform
**Out of scope:**
- Hosting YourTube on a server for other people (it stays local and single-user).
- Replacing the native setup. Docker is an extra option.
- Changing how playlists are read.

## Why this plan exists (user questions, 2026-10-30)
1. `systemctl --user restart yourtube` only works on Linux. What about macOS and Windows?
2. Could Docker make setup the same everywhere?
3. Why does the plan only talk about NVIDIA graphics cards? What about AMD?

## Where YourTube stands today

| Area | Linux | macOS | Windows |
|---|---|---|---|
| Keep it running | `scripts/service.sh install` (systemd user service) | `scripts/service.sh install` (LaunchAgent) | `scripts\service.ps1 -Install` (Task Scheduler, **untested**) |
| Restart after an update | `systemctl --user restart yourtube` | uninstall + install | uninstall + install |
| Read the browser sign-in | Chrome (keyring) / Firefox | Chrome (Keychain prompt) / Firefox | Firefox or cookies.txt (Chrome blocks it) |

### Why everything says "NVIDIA" today
There are three places, and only one is a hard limit:

| Part | What it uses now | NVIDIA only? | Why |
|---|---|---|---|
| **Local AI models** (ratings, summaries, Ask, categories) | Ollama | **No.** Ollama itself runs on NVIDIA (CUDA), **AMD (ROCm on Linux and Windows)** and **Apple Silicon (Metal)**, and on the CPU. | Works already. But YourTube's machine check doesn't see non-NVIDIA cards (next row). |
| **Machine check and model recommendation** (`local_setup.gpu()`, `RECOMMENDED` by graphics memory) | `nvidia-smi` | **Yes, by accident.** | Only `nvidia-smi` is queried. An AMD, Intel or Apple computer shows "no GPU" and is offered a CPU-sized model, although Ollama would use its GPU. **This is a YourTube gap, easy to fix.** |
| **Local transcription** (`local_asr.py`, faster-whisper) | CTranslate2 with CUDA (`ctranslate2.get_cuda_device_count()`, NVIDIA cuBLAS/cuDNN wheels in `requirements-local.txt`) | **Yes, today.** | faster-whisper runs on CTranslate2. Its standard PyPI builds include only NVIDIA CUDA for GPUs, so other cards fall back to the CPU (`small` model, about 4× slower than the GPU path). |
| GPU temperature pause (`local_asr.gpu_temperature`) | `nvidia-smi` | Yes | Only an NVIDIA reading is implemented; on other cards the pause is simply skipped. |
| Slide reading (`local_visual.py`) | Ollama vision model | No | Follows Ollama, so it already works wherever Ollama does. |

**In short:** the AI models already work on AMD and Apple; YourTube just doesn't detect those cards. Only **transcription** is truly NVIDIA-bound, because of the library it uses.

## Design notes

### A. One restart command everywhere (small)
- `scripts/service.sh` gains `restart`:
  - Linux: `systemctl --user restart yourtube.service`;
  - macOS: `launchctl kickstart -k gui/$(id -u)/com.yourtube.dashboard`.
- `scripts/service.ps1` gains `-Restart`: `Stop-ScheduledTask` then `Start-ScheduledTask`.
- `scripts/service.sh update` (optional): runs `git pull`, reinstalls requirements into `.venv`, then `restart`.
- The docs use `bash scripts/service.sh restart` everywhere instead of `systemctl`.

### B. Detect every kind of graphics card (machine check)
- `local_setup.gpu()` returns `{ok, vendor, name, memory_mb, detail}` from the first source that answers:
  1. NVIDIA: `nvidia-smi` (as now).
  2. AMD on Linux: `rocm-smi --showproductname --showmeminfo vram --json`, or `/sys/class/drm/card*/device/mem_info_vram_total` when rocm-smi is absent.
  3. AMD/Intel on Windows: `Get-CimInstance Win32_VideoController` (name; video memory is unreliable above 4 GB, so treat it as "at least").
  4. Apple Silicon: `sysctl -n machdep.cpu.brand_string` + `hw.memsize`. Unified memory: count about 60–70 % of it as usable for models.
  5. Fallback: Ollama's own report (`/api/ps` shows `size_vram` once a model is loaded).
- `RECOMMENDED` keeps its memory steps but takes this new reading. The page says which card was found, and whether Ollama will use it.
- Tests use fake command outputs for each vendor.

### C. Transcription on AMD, Intel and Apple (choose after research)
Options, best first. **Each needs a research spike before choosing (task R1), because support changes often:**

| Option | AMD | Apple | Intel | Effort | Notes |
|---|---|---|---|---|---|
| **whisper.cpp** (via its CLI or `pywhispercpp`) | Vulkan, or HIP/ROCm | Metal | Vulkan / SYCL | Medium | One engine for every vendor. Needs a GGUF/ggml model download instead of the faster-whisper one. Quality similar at the same model size. |
| CTranslate2 ROCm builds | ROCm (Linux, maybe Windows) | No | No | Small if it works | Keeps faster-whisper. **To verify:** whether current CTranslate2 releases ship ROCm wheels and which cards they support. |
| `mlx-whisper` | No | Yes (Apple Silicon) | No | Small | Apple only; very fast on M-series chips. |
| PyTorch + openai-whisper (ROCm build) | ROCm (Linux) | MPS (partial) | No | Large | Heavy install (several GB); last resort. |

- Proposed shape: `local_asr` gets a small **engine** choice (`settings["asr_engine"]`: `auto` / `faster-whisper` / `whisper.cpp` / `mlx`). `auto` picks:
  - faster-whisper on NVIDIA;
  - mlx on Apple Silicon;
  - whisper.cpp on AMD/Intel;
  - faster-whisper on the CPU otherwise.

  Every engine returns the same segments `[{start, dur, text}]` and language, so nothing downstream changes.
- The GPU temperature pause reads `rocm-smi`, or skips quietly, on non-NVIDIA cards.
- `requirements-local.txt` splits by platform; the setup scripts pick the right extra.

### D. Optional Docker setup
- **Files:** `Dockerfile` (python:3.12-slim + ffmpeg, `pip install -r requirements.txt`) and `docker-compose.yml`:
  - service `yourtube`, `restart: unless-stopped`;
  - port `127.0.0.1:8765:8765` (published only on this computer);
  - volume `./data:/data`, holding the database, settings, categories, logs and frames. New env vars `YTKB_DATA_DIR`, or the existing `YTKB_DB` / `YTKB_SETTINGS` / `YTKB_TAXONOMY` / `YTKB_LOG_DIR` pointing into `/data`.
- **Dashboard binding:** inside the container it must listen on `0.0.0.0`. Add `--host` (default `127.0.0.1`) and use it only in the container. The access token, the localhost Host-header check and the `X-YTKB` header stay as they are. The published port is bound to `127.0.0.1`, so nothing is exposed to the network.
- **Signing in to YouTube:** the container can't read the host browser's cookies. Private playlists and Watch Later need a `cookies.txt` export placed in `./data`. The guide explains how to export one and that it expires. Public playlists need nothing.
- **Local AI:** Ollama stays on the host. The compose file sets `OLLAMA_HOST=http://host.docker.internal:11434`, which needs `extra_hosts: ["host.docker.internal:host-gateway"]` on Linux. An optional `ollama` service profile could run Ollama in a container too.
- **GPU in Docker** (optional profiles):
  - NVIDIA: `deploy.resources.reservations.devices` with the NVIDIA Container Toolkit (Linux, and Windows via WSL2).
  - AMD: `devices: [/dev/kfd, /dev/dri]` with a ROCm-capable image (Linux only).
  - macOS: Docker can't use the Apple GPU, so transcription in Docker runs on the CPU there. Native install recommended for Mac users who transcribe.
- **The access link:** `docker compose exec yourtube python dashboard.py --print-url`.
- **When Docker is a good choice:** Windows users, people who don't want Python on their computer, and anyone using cloud AI or only public playlists. **When native is better:** private playlists via the browser sign-in, and GPU transcription on a Mac.

### E. Docs
- `docs/SETUP.md`:
  - a "Choose how to run it" table (native vs Docker, per OS);
  - a "Graphics cards" section saying what uses the GPU on NVIDIA / AMD / Intel / Apple, and what falls back to the CPU;
  - the restart command.
- `README.md` quick start mentions Docker as an option.

## Tasks (when the user approves)

### R1: Research spike: transcription on non-NVIDIA cards
- **worker:** claude
- **status:** todo
- **steps:**
  1. Check current CTranslate2 ROCm support.
  2. Check whisper.cpp Vulkan/HIP/Metal builds and Python bindings.
  3. Check mlx-whisper.
  4. Run one 10-minute clip through each engine available on this machine (CPU here; note what can't be tested).
  5. Record speed and quality against faster-whisper in `spikes/`.
- **output:** a recommendation in this plan (option for AMD, for Apple, for Intel).

### T1: `service.sh restart` / `service.ps1 -Restart` (+ optional `update`)
- **worker:** agy · **status:** todo · **files:** `scripts/service.sh`, `scripts/service.ps1`, `docs/SETUP.md`

### T2: Detect AMD, Intel and Apple graphics in the machine check
- **worker:** codex · **status:** todo · **files:** `local_setup.py`, `tests/test_local.py`, `web/index.html` (the Local AI card shows vendor and name)

### T3: Transcription engine choice (after R1)
- **worker:** claude (design) + codex · **status:** todo · **files:** `local_asr.py`, `voice.py`, `requirements-local*.txt`, `scripts/setup.*`, tests

### T4: Optional Docker setup
- **worker:** codex · **status:** todo · **files:** `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `dashboard.py` (`--host`), `docs/SETUP.md`

### T5: Docs and manual tests
- **worker:** claude · **status:** todo · **files:** `docs/SETUP.md`, `README.md`, `docs/MANUAL-TESTS.md` (a session per platform the user can test)

## Open questions for the user
- Which other systems will you actually use (Windows with which GPU, a Mac with which chip)? That decides what R1 tests first.
- Should the Docker image include local transcription (larger image) or stay small and leave transcription to the native install?

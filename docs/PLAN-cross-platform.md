# Plan: Run anywhere: one restart command, optional Docker, GPUs beyond NVIDIA

**Status: in progress since 2026-10-30 (`/conduct-run`), branch `feat/cross-platform`.** Kept in `docs/` (tracked) so it can be read on other systems.

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
- `local_setup.gpu()` returns `{ok, vendor, name, vram_mb, used_mb, detail}` from the first source that answers. Keep the existing key names (`vram_mb`, `used_mb`): `status()` and `recommend()` read `vram_mb`. `vendor` is new; `used_mb` may be missing when a source can't report it.
  1. NVIDIA: `nvidia-smi` (as now).
  2. AMD on Linux: `rocm-smi --showproductname --showmeminfo vram --json`, or `/sys/class/drm/card*/device/mem_info_vram_total` when rocm-smi is absent.
  3. AMD/Intel on Windows: `Get-CimInstance Win32_VideoController` (name; video memory is unreliable above 4 GB, so treat it as "at least").
  4. Apple Silicon: `sysctl -n machdep.cpu.brand_string` + `hw.memsize`. Unified memory: count about 60–70 % of it as usable for models.
  5. Fallback: Ollama's own report (`/api/ps` shows `size_vram` once a model is loaded).
- `RECOMMENDED` keeps its memory steps but takes this new reading. The page says which card was found, and whether Ollama will use it.
- **Intel is not settled.** Ollama's official GPU support covers NVIDIA, AMD and Apple; Intel GPU support is experimental at best (R1 checks it). Until then an Intel card is shown as "found, Ollama may not use it" and the recommendation stays CPU-sized.
- Tests use fake command outputs for each vendor.

### C. Transcription on AMD, Intel and Apple (choose after research)
**Decided after R1 (2026-10-30), see `spikes/asr-engines.md` and T3:** AMD Radeon → faster-whisper on CTranslate2's ROCm build; Apple Silicon → mlx-whisper; everything else → faster-whisper on the CPU. whisper.cpp and the `asr_engine` setting are dropped. The table below is the pre-research list.

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
- **Files:** `Dockerfile` (python:3.12-slim + ffmpeg + a JS runtime (Deno or Node), `pip install -r requirements.txt`, `CMD` runs `dashboard.py --no-open --host 0.0.0.0`) and `docker-compose.yml`:
  - service `yourtube`, `restart: unless-stopped`;
  - port `127.0.0.1:8765:8765` (published only on this computer);
  - volume `./data:/data`, holding the database, settings, categories, logs, frames and the media cache.
- **JS runtime:** `enrich.py` (`_js_runtimes`) hands Deno/Node/Bun to yt-dlp, which YouTube now needs for full video reads. Without one in the image, video details would fail in Docker.
- **Data folder (`YTKB_DATA_DIR`, new):** the existing `YTKB_DB` / `YTKB_SETTINGS` / `YTKB_TAXONOMY` / `YTKB_LOG_DIR` cover the database, settings, categories (`taxonomy-N.json` follows `YTKB_TAXONOMY`'s folder) and logs. But `frames/` and `media_cache/` are fixed to the repo folder today (`local_visual.CACHE` / `FRAMES`, `dashboard.FRAMES_DIR`, `local_asr.CACHE`, `voice.VOICE_DIR`). `YTKB_DATA_DIR` must become the default parent for all of these, so the slide pictures survive when the container is recreated. Unset = today's paths, so native installs don't move.
- **Dashboard binding:** inside the container it must listen on `0.0.0.0`. Add `--host` (default `127.0.0.1`) and use it only in the container. The access token, the localhost Host-header check and the `X-YTKB` header stay as they are. The published port is bound to `127.0.0.1`, so nothing is exposed to the network. **Warn** (a comment in the compose file and in the guide): changing the mapping to `8765:8765` publishes the dashboard to the local network, and the Host-header check doesn't stop direct access from other computers.
- **Signing in to YouTube:** the container can't read the host browser's cookies. Private playlists and Watch Later need a `cookies.txt` export placed in `./data`, and the Sources page is given the **container** path (`/data/cookies.txt`); a host path saved by an earlier native install won't be found. The guide explains how to export one and that it expires. Public playlists need nothing.
- **Local AI:** Ollama stays on the host. YourTube does **not** read `OLLAMA_HOST`: the address comes from `settings.json` (`providers.ollama.base_url`, default `http://localhost:11434`, see `llm.py`), and `localhost` inside the container is the container itself. So either:
  - add an env var (e.g. `YTKB_OLLAMA_URL`) that `load_settings()` uses as the default base URL when none is saved, and set it to `http://host.docker.internal:11434` in the compose file; or
  - have the guide tell Docker users to enter that address in Settings → Local AI.

  The first is friendlier; a saved base URL always wins. LM Studio's default (`http://localhost:1234/v1`) has the same problem. `host.docker.internal` needs `extra_hosts: ["host.docker.internal:host-gateway"]` on Linux. An optional `ollama` service profile could run Ollama in a container too.
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

## Decisions (user, 2026-10-30)
- **Go ahead:** the user started the run with `/conduct-run` on 2026-10-30.
- **Docker and Ollama:** automatic. New env var `YTKB_OLLAMA_URL`; the compose file sets it to `http://host.docker.internal:11434`.
- **Docker image:** stays small. No local transcription in the image; transcription stays a native-install feature.
- **Systems that matter:** Windows + NVIDIA, Windows + AMD, Mac (Apple Silicon), Linux + AMD/Intel. All four count for R1.
- **R1 scope:** web research, plus a CPU test on this machine (install whisper.cpp Python bindings into `.venv` for the spike only; don't add them to any requirements file in R1).
- **Controller choices:** all `docs/SETUP.md` and `README.md` changes move to T5, so no two tasks share a file. The Ollama `/api/ps` fallback (B.5) is dropped: it only answers once a model is loaded.

## Tasks

### R1: Research spike: transcription on non-NVIDIA cards
- **worker:** controller
- **status:** done (`spikes/asr-engines.md`)
- **files:** `spikes/asr-engines.md` (and a throwaway script under `spikes/`)
- **steps:**
  1. Check current CTranslate2 ROCm support (wheels, cards, Windows).
  2. Check whisper.cpp Vulkan/HIP/Metal builds and Python bindings (`pywhispercpp` and others): install story on Windows, macOS and Linux.
  3. Check mlx-whisper on Apple Silicon.
  4. Check whether current Ollama uses Intel GPUs (Arc / integrated) and AMD on Windows; this decides what the machine check promises.
  5. Run one 10-minute clip through faster-whisper (CPU) and whisper.cpp (CPU) here; note what can't be tested.
  6. Write speed and quality notes and a recommendation per vendor into `spikes/asr-engines.md`, and the chosen design into this plan's section C.
- **acceptance:**
  - [ ] a recommendation for AMD, Apple and Intel, each with how it installs on each OS
  - [ ] measured CPU numbers for the engines tried
- **verify:** `.venv/bin/python -m unittest`

### T1: `service.sh restart` + `update`, `service.ps1 -Restart`
- **worker:** codex
- **status:** done
- **files:** `scripts/service.sh`, `scripts/service.ps1`
- **steps:**
  1. `service.sh`: add `restart` and `update` to the argument `case`, the usage lines and the header comment.
  2. `restart`: Linux `systemctl --user restart yourtube.service`; macOS `launchctl kickstart -k "gui/$(id -u)/com.yourtube.dashboard"`. If the service isn't installed (Linux: `systemctl --user cat yourtube.service` fails; macOS: the plist file `~/Library/LaunchAgents/com.yourtube.dashboard.plist` is missing), print `Error: YourTube service is not installed. Run: bash scripts/service.sh install` and exit 1. Other OS: the existing "unsupported operating system" error.
  3. `update`: `git -C "$ROOT" pull --ff-only` (on failure print `Error: git pull failed; fix it and run this again` and exit 1); reinstall `requirements.txt` the same way `scripts/setup.sh` does (`.venv/bin/python -m pip` when the venv has pip, else `python3 -m pip --python .venv/bin/python`); also `requirements-local.txt` when `.venv/bin/python -c "import faster_whisper"` succeeds; then run the `restart` steps when the service is installed, else print `Updated. Start it with: bash scripts/service.sh install`.
  4. `service.ps1`: add a `-Restart` switch (and positional `restart`): if `Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue` finds nothing, warn `YourTube service is not installed. Run scripts\service.ps1 -Install` and exit 1; else `Stop-ScheduledTask`, then `Start-ScheduledTask`, then print `YourTube dashboard restarted.` Update the usage lines and the header comment.
- **acceptance:**
  - [ ] `bash -n scripts/service.sh` passes; `bash scripts/service.sh --help` lists restart and update
  - [ ] no change to install/uninstall/status/open behaviour
- **verify:** `bash -n scripts/service.sh && .venv/bin/python -m unittest`

### T2: Detect AMD, Intel and Apple graphics in the machine check
- **worker:** codex
- **status:** done
- **files:** `local_setup.py`, `tests/test_local.py`
- **steps:**
  1. `gpu(system: Optional[str] = None, drm: str = "/sys/class/drm") -> Dict` (`system` defaults to `platform.system()`; `drm` lets tests point at a temp folder). Returns `{ok, vendor, name, vram_mb, used_mb?, ollama_gpu, detail}`; `vendor` is `nvidia` / `amd` / `intel` / `apple` / `None`. Order, first answer wins:
     1. NVIDIA: `nvidia-smi` exactly as now, plus `vendor: "nvidia"`, `ollama_gpu: True`.
     2. Linux: each `<drm>/card*/device/` whose `vendor` file is `0x1002` (AMD) or `0x8086` (Intel). AMD: `vram_mb` from `mem_info_vram_total` (bytes), `used_mb` from `mem_info_vram_used` when present, name from `product_name` when present else `"AMD Radeon GPU"`, `ollama_gpu: True`. Intel: name `"Intel graphics"`, `vram_mb: 0`, `ollama_gpu: False`. Prefer an AMD card over an Intel one.
     3. Windows: `powershell -NoProfile -Command "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM | ConvertTo-Json"` through `_run` (timeout 10). Parse a dict or a list. Vendor from the name (`AMD`/`Radeon` → amd, `Intel` → intel, `NVIDIA` → nvidia). AMD: `vram_mb` = AdapterRAM in MB (the field caps at 4 GB, so the detail says "at least"), `ollama_gpu: True`. Intel: `ollama_gpu: False`.
     4. macOS on `arm64` (`platform.machine()`): `sysctl -n machdep.cpu.brand_string` (name) and `sysctl -n hw.memsize` (bytes). `vram_mb` = 65 % of memory (unified memory), `ollama_gpu: True`, detail like `Apple M2, 16 GB shared memory (about 10.4 GB usable for models)`.
     5. Nothing found: `{ok: False, vendor: None, ollama_gpu: False, detail: "No supported GPU found. Local models run on the CPU (slower)."}`.
  2. `detail` names the card and, for Intel, says `Ollama may not use this GPU yet; models are sized for the CPU.` `ok` is True for any card found.
  3. `status()` sizes recommendations from `g["vram_mb"]` only when `g["ok"] and g.get("ollama_gpu")`, else 0.
  4. `main()` (CLI) keeps working with the new fields.
  5. Tests in `tests/test_local.py`: patch `shutil.which`, `local_setup._run`, `platform.system`/`platform.machine` and use a temp `drm` folder. One case each: NVIDIA, AMD Linux sysfs, Intel Linux only, Windows AMD (single dict JSON), Windows list with Intel + AMD, Apple Silicon, nothing found, and `status()` giving CPU-sized picks for Intel.
- **acceptance:**
  - [ ] existing `vram_mb` / `used_mb` keys and the NVIDIA path unchanged
  - [ ] no new dependencies; every command through `_run`; a missing tool or bad output never raises
  - [ ] the Local AI card in `web/index.html` needs no change (it shows `detail`)
- **verify:** `.venv/bin/python -m unittest`

### T3: Transcription on Apple Silicon (mlx-whisper) and AMD (ROCm), from R1
- **worker:** codex
- **status:** todo
- **depends on:** R1, T4
- **files:** `local_asr.py`, `local_setup.py`, `requirements-local.txt`, `requirements-nvidia.txt` (new), `scripts/setup.sh`, `scripts/setup.ps1`, `scripts/service.sh`, `tests/test_asr.py`, `tests/test_local.py`
- **design (R1, `spikes/asr-engines.md`):** NVIDIA unchanged. AMD Radeon uses faster-whisper on CTranslate2's ROCm build (same code path, `device="cuda"`; installed by hand from the guide for now). Apple Silicon uses mlx-whisper. Everything else stays on faster-whisper on the CPU. No `asr_engine` setting: the engine follows from the hardware.
- **steps:**
  1. `local_asr.device()`: return `"mlx"` when `sys.platform == "darwin"`, `platform.machine() == "arm64"` and `importlib.util.find_spec("mlx_whisper")` is found; otherwise as now. `MODELS["mlx"] = ("mlx-community/whisper-large-v3-turbo", "float16")`, `SPEED["mlx"] = 15.0`.
  2. `choose_model(settings, "mlx")`: `asr_model` `auto`, or any name not starting with `mlx-community/`, gives the turbo repo; a name starting with `mlx-community/` is used as is. Other devices unchanged.
  3. New `MlxTranscriber(name, dev, compute)` with the same `__call__(samples, progress) -> (segments, language)` as `Transcriber`: `mlx_whisper.transcribe(samples, path_or_hf_repo=name, verbose=None)`; result segments → `{"start": round(start, 2), "dur": round(end - start, 2), "text": stripped}` (skip empty text); call `progress(1.0)` at the end (mlx-whisper has no progress callback); return `result["language"]`.
  4. `run_asr`: when `make_transcriber is Transcriber` and the device is `"mlx"`, use `MlxTranscriber`. Unload Ollama before loading when the device is `"cuda"` or `"mlx"` (mlx shares memory with Ollama). The start line says `the GPU` for both. Tests keep injecting `make_transcriber` (device stays `"cpu"` then).
  5. `local_asr.available()` and `local_setup.whisper()`: also true when only `mlx_whisper` is installed; `whisper()` detail names the engine found.
  6. `local_asr.gpu_temperature(drm="/sys/class/drm")`: when `nvidia-smi` is missing, return the highest `hwmon*/temp1_input` (millidegrees → °C) of any `card*/device` whose `vendor` is `0x1002`; `None` when nothing is readable. No change to `_cool_down`.
  7. Requirements: move the two `nvidia-*` lines from `requirements-local.txt` to a new `requirements-nvidia.txt` (same markers, header comment). Add `mlx-whisper; sys_platform == "darwin" and platform_machine == "arm64"` to `requirements-local.txt`.
  8. `local_setup.py` CLI: `--gpu-vendor` prints only `gpu()["vendor"]` (or `none`).
  9. `scripts/setup.sh` / `setup.ps1` step 4b: install `requirements-local.txt`; then `requirements-nvidia.txt` only when `local_setup.py --gpu-vendor` prints `nvidia`. When it prints `amd`, print: `For transcription on a Radeon GPU, see "Graphics cards" in docs/SETUP.md (CTranslate2 ROCm build).` Size wording in the question: about 1.2 GB with the NVIDIA libraries, about 150 MB without. `scripts/service.sh update`: same rule for `requirements-nvidia.txt`.
  10. Tests: `device()` → `"mlx"` (patch `sys.platform`, `platform.machine`, `importlib.util.find_spec`); `choose_model` for mlx; `MlxTranscriber` with a fake `mlx_whisper` module in `sys.modules`; `run_asr` picks `MlxTranscriber` when `device()` says mlx (patch `device` and `MlxTranscriber`); `gpu_temperature` from a temp `drm` folder (AMD card, none, unreadable); `--gpu-vendor`.
- **acceptance:**
  - [ ] on Linux/Windows with NVIDIA nothing changes (same models, same libraries installed)
  - [ ] no new dependency outside Apple Silicon; mlx is optional and only imported on that path
  - [ ] segments and language keep the `[{start, dur, text}]`, `str` shape
- **verify:** `bash -n scripts/setup.sh && bash -n scripts/service.sh && .venv/bin/python -m unittest`

### T4: Optional Docker setup
- **worker:** codex
- **status:** done
- **files:** `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.gitignore`, `dashboard.py`, `llm.py`, `storage.py`, `classifier.py`, `applog.py`, `local_asr.py`, `local_visual.py`, `voice.py`, `tests/test_docker_setup.py`
- **steps:**
  1. **`YTKB_DATA_DIR`:** each module that builds a data path reads `os.environ.get("YTKB_DATA_DIR")` at import time (same pattern as `YTKB_DB`) and uses it as the parent folder instead of the repo folder when set. The specific variables still win. Affected: `storage.DB_PATH` (`yt_kb.db`), `llm.SETTINGS_PATH` (`settings.json`), `classifier.TAXONOMY_PATH` (`taxonomy.json`), `applog.LOG_DIR` (`logs`), `local_asr.CACHE` (`media_cache/asr`), `local_visual.CACHE` / `FRAMES`, `voice.VOICE_DIR` (`media_cache/voice`), `dashboard.FRAMES_DIR` (`frames`). Code paths (`WEB_DIR`, `DOWNLOADS` scripts and docs) stay in the repo. Unset = exactly today's paths.
  2. **`YTKB_OLLAMA_URL`:** in `llm.load_settings()`, after loading, if the env var is set and the saved Ollama `base_url` is empty or `http://localhost:11434` / `http://127.0.0.1:11434`, use the env value (through `ollama_base`). Any other saved address wins. Do not write it back to the file.
  3. **`dashboard.py --host`** (default `127.0.0.1`), used for the `ThreadingHTTPServer` bind only. `ALLOWED_HOSTS`, the token check, `X-YTKB`, `--print-url` (still `http://127.0.0.1:<port>`) and `_probe_job` stay as they are. When the host isn't `127.0.0.1`/`localhost`/`::1`, log a warning: `Warning: listening on <host>; keep the port published to 127.0.0.1 only.`
  4. **`Dockerfile`:** `python:3.12-slim`; `apt-get install --no-install-recommends ffmpeg` plus Node.js (Debian `nodejs` package) for yt-dlp's JS challenges; copy the repo except what `.dockerignore` drops; `pip install --no-cache-dir -r requirements.txt` (no `requirements-local.txt`); `ENV YTKB_DATA_DIR=/data`; `EXPOSE 8765`; run as a non-root user owning `/data`; `CMD ["python", "dashboard.py", "--no-open", "--host", "0.0.0.0"]`.
  5. **`docker-compose.yml`:** service `yourtube` built from `.`, `restart: unless-stopped`, `ports: ["127.0.0.1:8765:8765"]` with a comment that removing `127.0.0.1:` exposes the dashboard to the network, `volumes: ["./data:/data"]`, `environment: YTKB_OLLAMA_URL=http://host.docker.internal:11434`, `extra_hosts: ["host.docker.internal:host-gateway"]`. A commented-out NVIDIA GPU block is not needed (no transcription in the image).
  6. **`.dockerignore`:** `.venv`, `.git`, `*.db*`, `settings.json`, `taxonomy*.json`, `cookies*.txt`, `media_cache`, `frames`, `logs`, `reports`, `data`, `__pycache__`, the AI files (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `HANDOFF.md`, `.claude`, `.conductor`, `.team`, `docs/plan`). **`.gitignore`:** add `data/`.
  7. **Tests** (`tests/test_docker_setup.py`): run `python -c` in a subprocess with a clean env (keep `PATH`, set `YTKB_DATA_DIR` to a temp dir, unset the other `YTKB_*`) and check every path from step 1 lands under it; with `YTKB_DATA_DIR` unset they stay in the repo; `YTKB_DB` still wins over `YTKB_DATA_DIR`; `YTKB_OLLAMA_URL` replaces a default saved address but not a custom one; `dashboard.main(["--host", ...])` parsing (patch the server class, or test the parser through a small helper).
- **acceptance:**
  - [ ] with no new env vars set, nothing moves and all existing tests pass
  - [ ] the localhost Host-header check, the token and `X-YTKB` are unchanged
  - [ ] no secrets or user data can end up in the image (`.dockerignore`)
- **verify:** `.venv/bin/python -m unittest`

### T5: Docs and manual tests
- **worker:** controller
- **status:** todo
- **depends on:** T1, T2, T3, T4
- **files:** `docs/SETUP.md`, `README.md`, `docs/MANUAL-TESTS.md`, `CLAUDE.md`
- **steps:**
  1. `docs/SETUP.md`: "Choose how to run it" (native vs Docker per OS), a Docker section (start, `--print-url` via `docker compose exec`, cookies.txt at `/data/cookies.txt`, Ollama on the host, the port warning), a "Graphics cards" section, `bash scripts/service.sh restart` / `update` and `service.ps1 -Restart` instead of `systemctl`, and the new env vars `YTKB_DATA_DIR`, `YTKB_OLLAMA_URL` and `--host`.
  2. `README.md` quick start mentions Docker. `CLAUDE.md` commands and env vars.
  3. `docs/MANUAL-TESTS.md`: one session per platform the user can test (Windows NVIDIA, Windows AMD, Mac, Linux AMD/Intel, Docker).
- **verify:** `.venv/bin/python -m unittest`

## Open questions for the user
- None left for now.

## Review notes (2026-10-30)
Checked against the code before any work started:
- Fixed in this plan: Ollama address in Docker (YourTube never read `OLLAMA_HOST`), `frames/` and `media_cache/` outside `/data`, the JS runtime missing from the image, `gpu()` key names, Intel GPU support marked as unsettled, the warning about the port mapping, and the container path for cookies.txt.
- Confirmed as written: `scripts/service.sh` (systemd + LaunchAgent label `com.yourtube.dashboard`), `scripts/service.ps1`, `local_setup.gpu()` / `RECOMMENDED`, `local_asr.gpu_temperature`, `dashboard.py --print-url`, `YTKB_LOG_DIR` (`applog.py`), the Local AI card in `web/index.html`.

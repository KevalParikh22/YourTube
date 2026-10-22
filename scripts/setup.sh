#!/usr/bin/env bash
# YT KB setup for Linux and macOS. Full guide: docs/SETUP.md
#   bash scripts/setup.sh            # app only: Python environment and dependencies
#   bash scripts/setup.sh --local-ai # also local AI: Ollama plus a model sized for this computer
#   options: -y/--yes (no questions), YTKB_MODEL=name (choose the model)
# Safe to run again: every step checks first and skips what is already done.
# It asks before installing anything system-wide (Ollama's installer may ask for your password).
set -euo pipefail

# Find the YT KB folder: where you run it from, next to this script, or one level up (scripts/)
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT=""
for d in "$PWD" "$HERE" "$(dirname "$HERE")"; do
  if [ -f "$d/dashboard.py" ] && [ -f "$d/requirements.txt" ]; then ROOT="$d"; break; fi
done
[ -n "$ROOT" ] || { echo "Run this inside the YT KB folder (the one with dashboard.py), e.g.: cd ~/yt-kb && bash setup.sh"; exit 1; }
cd "$ROOT"
LOCAL_AI=0; ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --local-ai) LOCAL_AI=1 ;;
    -y|--yes)   ASSUME_YES=1 ;;
    -h|--help)  sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg"; exit 2 ;;
  esac
done

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  ✅ %s\n' "$*"; }
warn() { printf '  ⚠️  %s\n' "$*"; }
ask()  { [ "$ASSUME_YES" = 1 ] && return 0; read -r -p "  $1 [y/N] " a; [[ "$a" =~ ^[Yy] ]]; }

say "1. Python"
PY=$(command -v python3 || true)
[ -n "$PY" ] || { warn "python3 not found. Install Python 3.10 or newer, then run this again."; exit 1; }
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))' || { warn "Python 3.10+ is needed (found $("$PY" -V))."; exit 1; }
ok "$("$PY" -V)"

say "2. Virtual environment (.venv) and dependencies"
if [ ! -x .venv/bin/python ]; then
  # Some Debian/Ubuntu Pythons lack ensurepip; fall back to a pip-less venv filled by the system pip
  "$PY" -m venv .venv 2>/dev/null || "$PY" -m venv --without-pip .venv
fi
if .venv/bin/python -m pip --version >/dev/null 2>&1; then
  .venv/bin/python -m pip install -q -r requirements.txt
else
  "$PY" -m pip --python .venv/bin/python install -q -r requirements.txt
fi
ok "Dependencies installed ($(.venv/bin/python -c 'import yt_dlp; print("yt-dlp", yt_dlp.version.__version__)'))"

say "3. ffmpeg (used later for local transcription and slides)"
if command -v ffmpeg >/dev/null; then ok "$(ffmpeg -version | head -1 | cut -d' ' -f1-3)"
else warn "Not installed. Install it with your package manager (e.g. sudo apt install ffmpeg, or brew install ffmpeg)."; fi

if [ "$LOCAL_AI" = 1 ]; then
  say "4. Ollama (runs AI models on this computer)"
  if ! command -v ollama >/dev/null; then
    if [ "$(uname)" = "Darwin" ]; then
      warn "Install Ollama for macOS from https://ollama.com/download, open it once, then run this script again."
      exit 1
    fi
    echo "  Ollama is not installed. Its official installer is: curl -fsSL https://ollama.com/install.sh | sh"
    if ask "Run Ollama's official installer now? It may ask for your password."; then
      curl -fsSL https://ollama.com/install.sh | sh
    else
      warn "Skipped. Install Ollama yourself, then run: bash scripts/setup.sh --local-ai"; exit 0
    fi
  fi
  ok "$(ollama --version 2>/dev/null | head -1)"

  if ! curl -fsS -m 3 http://localhost:11434/api/version >/dev/null 2>&1; then
    echo "  Starting Ollama…"
    (systemctl --user start ollama 2>/dev/null || sudo -n systemctl start ollama 2>/dev/null) || (nohup ollama serve >/dev/null 2>&1 &)
    for _ in $(seq 1 20); do curl -fsS -m 2 http://localhost:11434/api/version >/dev/null 2>&1 && break; sleep 1; done
  fi
  curl -fsS -m 3 http://localhost:11434/api/version >/dev/null 2>&1 || { warn "Ollama is not answering. Start it with: ollama serve"; exit 1; }
  ok "Ollama is running"

  say "4b. Local transcription (faster-whisper)"
  if .venv/bin/python -c 'import faster_whisper' 2>/dev/null; then ok "faster-whisper installed"
  elif ask "Install local transcription (faster-whisper; about 1.2 GB with the NVIDIA libraries)?"; then
    if .venv/bin/python -m pip --version >/dev/null 2>&1; then .venv/bin/python -m pip install -q -r requirements-local.txt
    else "$PY" -m pip --python .venv/bin/python install -q -r requirements-local.txt; fi
    ok "faster-whisper installed (the speech model downloads on first use: ~1.6 GB)"
  else warn "Skipped. Videos without captions can't be transcribed locally until you install it."; fi

  say "5. Model sized for this computer"
  MODEL="${YTKB_MODEL:-$(.venv/bin/python local_setup.py --recommended)}"
  echo "  Recommended: $MODEL (override with YTKB_MODEL=name bash scripts/setup.sh --local-ai)"
  if ollama list | awk 'NR>1 {print $1}' | grep -qx "$MODEL"; then ok "$MODEL already downloaded"
  elif ask "Download $MODEL now?"; then .venv/bin/python local_setup.py --pull "$MODEL"
  else warn "Skipped. Download it later from the AI page."; fi
  if ollama list | awk 'NR>1 {print $1}' | grep -qx "$MODEL"; then
    .venv/bin/python local_setup.py --use "$MODEL"
  fi

  say "6. Embedding model for questions and meaning-based search"
  EMBED="$(.venv/bin/python local_setup.py --embed-model)"
  if ollama list | awk 'NR>1 {print $1}' | grep -qx "$EMBED"; then ok "$EMBED already downloaded"
  elif ask "Download $EMBED (0.64 GB) so questions and search match by meaning, in any language?"; then
    .venv/bin/python local_setup.py --pull "$EMBED" && ok "The index builds after your next sync (or: .venv/bin/python main.py --embed)"
  else warn "Skipped. Questions and search use keywords only until you download it on the AI page."; fi
fi

say "Done"
.venv/bin/python local_setup.py || true
echo
echo "  Start the dashboard:  .venv/bin/python dashboard.py"

#!/usr/bin/env bash
# YourTube background service manager (Linux systemd and macOS LaunchAgent).
# Usage:
#   bash scripts/service.sh install [--port 8765]
#   bash scripts/service.sh uninstall
#   bash scripts/service.sh status
#   bash scripts/service.sh open [--port 8765]
set -euo pipefail

PORT=8765
CMD=""

while [ $# -gt 0 ]; do
  case "$1" in
    --port)
      [ $# -ge 2 ] || { echo "Error: --port requires an argument" >&2; exit 2; }
      PORT="$2"; shift 2 ;;
    --port=*)
      PORT="${1#*=}"; shift ;;
    install|uninstall|status|open)
      CMD="$1"; shift ;;
    -h|--help)
      echo "Usage: $0 {install|uninstall|status|open} [--port N]"
      exit 0 ;;
    *)
      echo "Error: unknown argument: $1" >&2
      echo "Usage: $0 {install|uninstall|status|open} [--port N]" >&2
      exit 2 ;;
  esac
done

if [ -z "$CMD" ]; then
  echo "Usage: $0 {install|uninstall|status|open} [--port N]" >&2
  exit 2
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT=""
for d in "$HERE" "$(dirname "$HERE")" "$PWD"; do
  if [ -f "$d/dashboard.py" ] && [ -f "$d/requirements.txt" ]; then
    ROOT="$(cd "$d" && pwd)"
    break
  fi
done

if [ -z "$ROOT" ]; then
  echo "Error: could not find repo root with dashboard.py" >&2
  exit 1
fi

PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "Error: .venv/bin/python not found in $ROOT. Run bash scripts/setup.sh first." >&2
  exit 1
fi

OS="$(uname -s)"

case "$CMD" in
  install)
    if [ "$OS" = "Linux" ]; then
      SERVICE_DIR="$HOME/.config/systemd/user"
      SERVICE_FILE="$SERVICE_DIR/yourtube.service"
      mkdir -p "$SERVICE_DIR"
      cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=YourTube dashboard
StartLimitIntervalSec=0

[Service]
WorkingDirectory=$ROOT
ExecStartPre=/usr/bin/test -d "$ROOT"
ExecStart="$ROOT/.venv/bin/python" "$ROOT/dashboard.py" --no-open --port $PORT
Restart=on-failure
RestartSec=15

[Install]
WantedBy=default.target
EOF
      systemctl --user daemon-reload
      systemctl --user enable --now yourtube.service
      echo "YourTube service installed and started."
      echo "Open the dashboard with: bash \"$0\" open"
    elif [ "$OS" = "Darwin" ]; then
      PLIST_DIR="$HOME/Library/LaunchAgents"
      PLIST_FILE="$PLIST_DIR/com.yourtube.dashboard.plist"
      mkdir -p "$PLIST_DIR"
      mkdir -p "$ROOT/logs"
      cat > "$PLIST_FILE" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.yourtube.dashboard</string>
    <key>ProgramArguments</key>
    <array>
        <string>$ROOT/.venv/bin/python</string>
        <string>$ROOT/dashboard.py</string>
        <string>--no-open</string>
        <string>--port</string>
        <string>$PORT</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$ROOT</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$ROOT/logs/service.log</string>
    <key>StandardErrorPath</key>
    <string>$ROOT/logs/service.log</string>
</dict>
</plist>
EOF
      UID_NUM="$(id -u)"
      launchctl bootout "gui/$UID_NUM" "$PLIST_FILE" 2>/dev/null || true
      launchctl bootstrap "gui/$UID_NUM" "$PLIST_FILE"
      echo "YourTube service installed and started."
      echo "Open the dashboard with: bash \"$0\" open"
    else
      echo "Error: unsupported operating system: $OS" >&2
      exit 1
    fi
    ;;

  uninstall)
    if [ "$OS" = "Linux" ]; then
      SERVICE_FILE="$HOME/.config/systemd/user/yourtube.service"
      systemctl --user disable --now yourtube.service 2>/dev/null || true
      rm -f "$SERVICE_FILE"
      systemctl --user daemon-reload 2>/dev/null || true
      echo "YourTube service uninstalled."
    elif [ "$OS" = "Darwin" ]; then
      PLIST_FILE="$HOME/Library/LaunchAgents/com.yourtube.dashboard.plist"
      UID_NUM="$(id -u)"
      launchctl bootout "gui/$UID_NUM" "$PLIST_FILE" 2>/dev/null || true
      rm -f "$PLIST_FILE"
      echo "YourTube service uninstalled."
    else
      echo "Error: unsupported operating system: $OS" >&2
      exit 1
    fi
    ;;

  status)
    if [ "$OS" = "Linux" ]; then
      systemctl --user status --no-pager yourtube.service
    elif [ "$OS" = "Darwin" ]; then
      UID_NUM="$(id -u)"
      launchctl print "gui/$UID_NUM/com.yourtube.dashboard" 2>/dev/null || launchctl list | grep com.yourtube.dashboard || echo "YourTube service not loaded."
    else
      echo "Error: unsupported operating system: $OS" >&2
      exit 1
    fi
    ;;

  open)
    URL="$("$PY" "$ROOT/dashboard.py" --print-url --port "$PORT")"
    if [ "$OS" = "Darwin" ]; then
      open "$URL"
    else
      if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$URL"
      elif command -v sensible-browser >/dev/null 2>&1; then
        sensible-browser "$URL"
      else
        echo "Open the dashboard link in your browser: $URL"
      fi
    fi
    ;;
esac

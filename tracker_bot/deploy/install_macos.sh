#!/bin/bash
# Installs a macOS LaunchAgent: the bot starts at login and restarts if it crashes.
# Usage: bash deploy/install_macos.sh [uninstall]
set -euo pipefail

LABEL="com.personal.trackerbot"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ "${1:-}" = "uninstall" ]; then
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Uninstalled. The bot no longer starts automatically."
    exit 0
fi

[ -f "$ROOT/.env" ] || { echo "Missing $ROOT/.env"; exit 1; }
[ -x "$ROOT/.venv/bin/python" ] || { echo "Missing $ROOT/.venv (run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)"; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/logs"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array><string>/bin/bash</string><string>$ROOT/deploy/run.sh</string></array>
    <key>WorkingDirectory</key><string>$ROOT</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>30</integer>
    <key>StandardOutPath</key><string>$ROOT/logs/bot.log</string>
    <key>StandardErrorPath</key><string>$ROOT/logs/bot.log</string>
</dict>
</plist>
PLISTEOF

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "Installed. The bot is running in the background."
echo "Logs: $ROOT/logs/bot.log"
echo "Remove with: bash deploy/install_macos.sh uninstall"

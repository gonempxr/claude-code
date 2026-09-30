#!/usr/bin/env python3
"""Points Claude Code's status line at claude_statusline.py.

Refuses to replace a status line you already have unless you pass --force.
Usage: python3 deploy/install_statusline.py [--force]
"""
import json
import os
import shutil
import sys
from pathlib import Path

home = Path(os.environ.get("CLAUDE_HOME") or Path.home() / ".claude")
settings_path = home / "settings.json"
script = Path(__file__).resolve().parent / "claude_statusline.py"
command = f"python3 {script}"

try:
    settings = json.loads(settings_path.read_text())
except FileNotFoundError:
    settings = {}
except ValueError:
    sys.exit(f"{settings_path} is not valid JSON; fix or remove it first.")

existing = settings.get("statusLine")
if existing and existing.get("command") != command and "--force" not in sys.argv:
    sys.exit(f"You already have a status line: {existing}\nRe-run with --force to replace it (a backup is saved).")

home.mkdir(parents=True, exist_ok=True)
if settings_path.exists():
    shutil.copy(settings_path, settings_path.with_suffix(".json.bak"))
settings["statusLine"] = {"type": "command", "command": command}
settings_path.write_text(json.dumps(settings, indent=2) + "\n")
print(f"Done. Restart Claude Code. Status line -> {command}")

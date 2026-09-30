#!/bin/bash
# Loads .env and runs the bot with the project's virtualenv. Used by launchd.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a
. ./.env
set +a
exec ./.venv/bin/python main.py

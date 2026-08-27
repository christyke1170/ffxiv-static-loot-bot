#!/usr/bin/env sh
set -eu
[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
[ -f .env ] || cp .env.example .env
.venv/bin/static-loot-db-upgrade
printf '%s\n' 'Setup complete. Edit .env, then run: .venv/bin/static-loot-bot'
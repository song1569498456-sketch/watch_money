#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi
.venv/bin/python -m pip install -e .
(cd frontend && npm ci && npm run build)
if [ ! -f .env ]; then cp .env.example .env; fi
exec .venv/bin/python -m uvicorn watch_money.app:app --host 127.0.0.1 --port 8000 --env-file .env

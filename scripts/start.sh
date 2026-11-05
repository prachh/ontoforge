#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Load this workspace's local provider configuration. Never source imported repositories.
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.lock.txt
fi
if [ ! -d web/node_modules ]; then npm ci --prefix web; fi
npm run build --prefix web
printf '\nStarting OntoForge at http://127.0.0.1:%s\n' "${PORT:-8017}"
exec .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port "${PORT:-8017}"

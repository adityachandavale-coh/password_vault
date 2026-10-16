#!/usr/bin/env sh
# Start HomeHub on macOS or Linux. Host, port, and certificate directory come
# from .env; anything passed here is forwarded to `python -m homehub`.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

PYTHON=.venv/bin/python
if [ ! -x "$PYTHON" ]; then
  BOOTSTRAP=$(command -v python3 || command -v python || true)
  if [ -z "$BOOTSTRAP" ]; then
    echo "Python 3.12 or newer is required but was not found on PATH." >&2
    exit 1
  fi
  "$BOOTSTRAP" -m venv .venv
  "$PYTHON" -m pip install --upgrade pip
  "$PYTHON" -m pip install -r requirements.txt
fi

exec "$PYTHON" -m homehub --host "${HOMEHUB_BIND:-0.0.0.0}" "$@"

#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
test -f data/certs/server.crt
test -f data/certs/server.key
exec .venv/bin/python -m uvicorn homehub.main:app --host 0.0.0.0 --port 8443 --ssl-certfile data/certs/server.crt --ssl-keyfile data/certs/server.key


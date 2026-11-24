#!/bin/sh
# Container start: create tables + demo data (idempotent), then serve on $PORT (Render) or 8000.
set -e
python -m scripts.seed
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"

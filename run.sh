#!/usr/bin/env bash
# Usage: ./run.sh   ->  http://localhost:8000
set -e
cd "$(dirname "$0")/backend"
[ -d ../.venv ] || python3 -m venv ../.venv
source ../.venv/bin/activate
pip install -q -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

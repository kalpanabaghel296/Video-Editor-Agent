#!/usr/bin/env bash
# Usage: ./run.sh   ->  http://localhost:8000
set -e
cd "$(dirname "$0")/backend"
[ -d ../.venv ] || python3 -m venv ../.venv
source ../.venv/bin/activate
python -m pip install -q -r requirements.txt
python -c "from app.utils.ffmpeg import ffmpeg_status as s; r=s(); print('FFmpeg OK: ' + str(r['version']) if r['ok'] else 'WARNING: ' + r['message'])"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

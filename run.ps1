Set-Location "$PSScriptRoot\backend"
if (-not (Test-Path "..\.venv")) {
    python -m venv ..\.venv
}
& "..\.venv\Scripts\Activate.ps1"
# "python -m ..." instead of pip.exe / uvicorn.exe: those .exe files can be blocked by Windows Application Control
python -m pip install -q -r requirements.txt
python -c "from app.utils.ffmpeg import ffmpeg_status as s; r=s(); print('FFmpeg OK: ' + str(r['version']) if r['ok'] else 'WARNING: ' + r['message'])"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

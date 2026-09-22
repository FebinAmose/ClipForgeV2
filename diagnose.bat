@echo off
cd /d "%~dp0"
where ffmpeg
ffmpeg -version
if exist .venv\Scripts\python.exe .venv\Scripts\python.exe -c "import fastapi,yt_dlp,faster_whisper;print('Python packages OK')"
pause

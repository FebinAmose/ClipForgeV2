@echo off
cd /d "%~dp0"
py -3 -m venv .venv
call .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if not exist .env copy .env.example .env >nul
echo Setup complete. Run start.bat
pause

@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (echo Run setup.bat first.&pause&exit /b 1)
call .venv\Scripts\activate
python -m app.main

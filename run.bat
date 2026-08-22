@echo off
REM Launch the UTR Extractor. Double-click this file, or run it from a terminal.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   Virtual environment not found.
    echo   Set it up once with:
    echo.
    echo       py -3.13 -m venv .venv
    echo       .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

echo Starting UTR Extractor on http://127.0.0.1:8000 ...
start "" http://127.0.0.1:8000
".venv\Scripts\python.exe" server.py --port 8000
pause

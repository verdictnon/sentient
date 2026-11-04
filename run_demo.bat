@echo off
rem Start Sentient with the simulated store. The first run installs dependencies.
setlocal
cd /d "%~dp0"
if not exist .venv-app\Scripts\python.exe (
  echo Creating the Python environment. This happens once.
  py -3.13 -m venv .venv-app 2>nul || py -3.12 -m venv .venv-app 2>nul || python -m venv .venv-app
  if errorlevel 1 (
    echo Install Python 3.12 or 3.13 from python.org, then run this again.
    pause
    exit /b 1
  )
  .venv-app\Scripts\python -m pip install --quiet --upgrade pip
  .venv-app\Scripts\python -m pip install --quiet -r requirements-app.txt
  if errorlevel 1 (
    rmdir /s /q .venv-app
    echo Installing dependencies failed. Check your connection and that this is Python 3.12 or 3.13.
    pause
    exit /b 1
  )
)
.venv-app\Scripts\python scripts\run_demo.py %*

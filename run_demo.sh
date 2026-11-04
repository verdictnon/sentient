#!/bin/sh
# Start Sentient with the simulated store. The first run installs dependencies.
set -e
cd "$(dirname "$0")"
if [ ! -x .venv-app/bin/python ]; then
  echo "Creating the Python environment. This happens once."
  python=""
  for candidate in python3.13 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      python=$candidate
      break
    fi
  done
  if [ -z "$python" ]; then
    echo "Install Python 3.12 or 3.13, then run this again." >&2
    exit 1
  fi
  "$python" -m venv .venv-app
  if ! .venv-app/bin/python -m pip install --quiet --upgrade pip ||
     ! .venv-app/bin/python -m pip install --quiet -r requirements-app.txt; then
    rm -rf .venv-app
    echo "Installing dependencies failed. Check your connection and that this is Python 3.12 or 3.13." >&2
    exit 1
  fi
fi
exec .venv-app/bin/python scripts/run_demo.py "$@"

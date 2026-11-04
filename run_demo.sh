#!/bin/sh
# Start Sentient with the simulated store. The first run installs dependencies.
set -e
cd "$(dirname "$0")"
if [ ! -x .venv-app/bin/python ]; then
  echo "Creating the Python environment. This happens once."
  python3 -m venv .venv-app
  if ! .venv-app/bin/python -m pip install --quiet --upgrade pip ||
     ! .venv-app/bin/python -m pip install --quiet -r requirements-app.txt; then
    rm -rf .venv-app
    echo "Installing dependencies failed. Check your connection and run this again." >&2
    exit 1
  fi
fi
exec .venv-app/bin/python scripts/run_demo.py "$@"

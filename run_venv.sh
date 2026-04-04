#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# run_venv.sh
# Launches the app inside an isolated virtual environment.
# Creates the venv and installs dependencies automatically if absent.
# -----------------------------------------------------------------------------

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/.venv"
VENV_PYTHON="$VENV/bin/python"
VENV_PIP="$VENV/bin/pip"
VENV_STREAMLIT="$VENV/bin/streamlit"
APP="$ROOT/app.py"
REQUIREMENTS="$ROOT/requirements.txt"

# 1. Locate system Python (prefer python3)
if command -v python3 &>/dev/null; then
    SYS_PYTHON="$(command -v python3)"
elif command -v python &>/dev/null; then
    SYS_PYTHON="$(command -v python)"
else
    echo "[ERROR] Python not found in PATH. Install Python 3.8+ and try again."
    exit 1
fi
echo "System Python : $SYS_PYTHON"

# 2. Create virtual environment if absent
if [ ! -f "$VENV_PYTHON" ]; then
    echo
    echo "Virtual environment not found. Creating at: $VENV"
    "$SYS_PYTHON" -m venv "$VENV"
    echo "Virtual environment created successfully."
else
    echo "Virtual environment : $VENV  [found]"
fi

# 3. Install / sync dependencies
echo
echo "Syncing dependencies from requirements.txt ..."
"$VENV_PIP" install -r "$REQUIREMENTS" --quiet --disable-pip-version-check
echo "All dependencies are up to date."

# 4. Launch the app
echo
echo "Starting PrairieLearn Exam Scheduler ..."
echo "Press Ctrl+C to stop the server."
echo
"$VENV_STREAMLIT" run "$APP"

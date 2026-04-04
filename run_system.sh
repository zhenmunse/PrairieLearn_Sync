#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# run_system.sh
# Launches the app using the system-wide Python installation.
# Checks each required package; installs from requirements.txt only when
# something is missing. No virtual environment is created.
# -----------------------------------------------------------------------------

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP="$ROOT/app.py"
REQUIREMENTS="$ROOT/requirements.txt"

# 1. Locate system Python (prefer python3)
if command -v python3 &>/dev/null; then
    PY="$(command -v python3)"
elif command -v python &>/dev/null; then
    PY="$(command -v python)"
else
    echo "[ERROR] Python not found in PATH. Install Python 3.8+ and try again."
    exit 1
fi
echo "Using Python : $PY"
echo

# 2. Check required packages
echo "Checking required packages ..."
NEED_INSTALL=0

check_pkg() {
    local module="$1"
    local display="$2"
    if "$PY" -c "import $module" &>/dev/null; then
        printf "  [OK]      %s\n" "$display"
    else
        printf "  [MISSING] %s\n" "$display"
        NEED_INSTALL=1
    fi
}

check_pkg "streamlit" "streamlit"
check_pkg "pandas"    "pandas"
check_pkg "requests"  "requests"
check_pkg "github"    "PyGithub"
check_pkg "pytz"      "pytz"

# 3. Install if needed
echo
if [ "$NEED_INSTALL" -eq 1 ]; then
    echo "Missing packages detected. Installing from requirements.txt ..."
    "$PY" -m pip install -r "$REQUIREMENTS" --quiet --disable-pip-version-check
    echo "Dependencies installed successfully."
else
    echo "All dependencies are satisfied. No installation needed."
fi

# 4. Launch the app
echo
echo "Starting PrairieLearn Exam Scheduler ..."
echo "Press Ctrl+C to stop the server."
echo
"$PY" -m streamlit run "$APP"

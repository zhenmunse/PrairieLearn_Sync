@echo off
setlocal EnableDelayedExpansion

:: ---------------------------------------------------------------------------
:: run_venv.bat
:: Launches the app inside an isolated virtual environment.
:: Creates the venv and installs dependencies automatically if absent.
:: ---------------------------------------------------------------------------

set "ROOT=%~dp0"
set "VENV=%ROOT%.venv"
set "VENV_PYTHON=%VENV%\Scripts\python.exe"
set "VENV_PIP=%VENV%\Scripts\pip.exe"
set "VENV_STREAMLIT=%VENV%\Scripts\streamlit.exe"
set "APP=%ROOT%app.py"
set "REQUIREMENTS=%ROOT%requirements.txt"

:: 1. Verify system Python
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH. Install Python 3.8+ and try again.
    pause
    exit /b 1
)
for /f "delims=" %%i in ('where python') do set "SYS_PYTHON=%%i" & goto :found_python
:found_python
echo System Python : %SYS_PYTHON%

:: 2. Create virtual environment if absent
if not exist "%VENV_PYTHON%" (
    echo.
    echo Virtual environment not found. Creating at: %VENV%
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo Virtual environment created successfully.
) else (
    echo Virtual environment : %VENV%  [found]
)

:: 3. Install / sync dependencies
echo.
echo Syncing dependencies from requirements.txt ...
"%VENV_PIP%" install -r "%REQUIREMENTS%" --quiet --disable-pip-version-check
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)
echo All dependencies are up to date.

:: 4. Launch the app
echo.
echo Starting PrairieLearn Exam Scheduler ...
echo Press Ctrl+C to stop the server.
echo.
"%VENV_STREAMLIT%" run "%APP%"

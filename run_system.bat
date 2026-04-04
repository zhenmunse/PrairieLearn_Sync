@echo off
setlocal EnableDelayedExpansion

:: ---------------------------------------------------------------------------
:: run_system.bat
:: Launches the app using the system-wide Python installation.
:: Checks each required package; installs from requirements.txt only when
:: something is missing. No virtual environment is created.
:: ---------------------------------------------------------------------------

set "ROOT=%~dp0"
set "APP=%ROOT%app.py"
set "REQUIREMENTS=%ROOT%requirements.txt"

:: 1. Verify system Python
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH. Install Python 3.8+ and try again.
    pause
    exit /b 1
)
for /f "delims=" %%i in ('where python') do set "PY=%%i" & goto :found_python
:found_python
echo Using Python : %PY%
echo.

:: 2. Check required packages
echo Checking required packages ...
set "NEED_INSTALL=0"

:: streamlit
python -c "import streamlit" >nul 2>&1
if errorlevel 1 ( echo   [MISSING] streamlit  & set "NEED_INSTALL=1" ) else echo   [OK]      streamlit

:: pandas
python -c "import pandas" >nul 2>&1
if errorlevel 1 ( echo   [MISSING] pandas     & set "NEED_INSTALL=1" ) else echo   [OK]      pandas

:: requests
python -c "import requests" >nul 2>&1
if errorlevel 1 ( echo   [MISSING] requests   & set "NEED_INSTALL=1" ) else echo   [OK]      requests

:: PyGithub (imported as 'github')
python -c "import github" >nul 2>&1
if errorlevel 1 ( echo   [MISSING] PyGithub   & set "NEED_INSTALL=1" ) else echo   [OK]      PyGithub

:: pytz
python -c "import pytz" >nul 2>&1
if errorlevel 1 ( echo   [MISSING] pytz       & set "NEED_INSTALL=1" ) else echo   [OK]      pytz

:: 3. Install if needed
echo.
if "%NEED_INSTALL%"=="1" (
    echo Missing packages detected. Installing from requirements.txt ...
    python -m pip install -r "%REQUIREMENTS%" --quiet --disable-pip-version-check
    if errorlevel 1 (
        echo [ERROR] Installation failed. Check your pip configuration.
        pause
        exit /b 1
    )
    echo Dependencies installed successfully.
) else (
    echo All dependencies are satisfied. No installation needed.
)

:: 4. Launch the app
echo.
echo Starting PrairieLearn Exam Scheduler ...
echo Press Ctrl+C to stop the server.
echo.
python -m streamlit run "%APP%"

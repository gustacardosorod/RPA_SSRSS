@echo off
setlocal EnableExtensions

set "BASE_DIR=%~dp0.."
cd /d "%BASE_DIR%"

if exist "%BASE_DIR%\.venv\Scripts\activate.bat" (
    call "%BASE_DIR%\.venv\Scripts\activate.bat"
)

streamlit run "%BASE_DIR%\app.py"

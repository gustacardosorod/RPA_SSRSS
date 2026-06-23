@echo off
setlocal EnableExtensions EnableDelayedExpansion
set "BASE_DIR=%~dp0.."
set "ASSINATURAS=H:\Groups\VAB_TQC\SGQ - 2021\1. SGQ - 2023\4. Demanda de Dados\28. BI Clientes\Assinaturas"
cd /d "%BASE_DIR%"
if exist "%BASE_DIR%\.venv\Scripts\activate.bat" call "%BASE_DIR%\.venv\Scripts\activate.bat"

python "%BASE_DIR%\rpa_ssrs_assinaturas.py" --assinaturas "%ASSINATURAS%"
pause

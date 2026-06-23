@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ============================================================
REM RPA SSRS - Assinaturas -> Snapshot -> ETL -> Banco -> Limpeza
REM Ajuste o caminho UNC abaixo. Em Tarefa Agendada, drive H: costuma sumir.
REM ============================================================

set "BASE_DIR=%~dp0.."
set "ASSINATURAS=H:\Groups\VAB_TQC\SGQ - 2021\1. SGQ - 2023\4. Demanda de Dados\28. BI Clientes\Assinaturas"
set "LOG_DIR=%BASE_DIR%\LOGS"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
cd /d "%BASE_DIR%"

if exist "%BASE_DIR%\.venv\Scripts\activate.bat" (
    call "%BASE_DIR%\.venv\Scripts\activate.bat"
)

python "%BASE_DIR%\rpa_ssrs_assinaturas.py" ^
  --assinaturas "%ASSINATURAS%" ^
  --enviar-banco ^
  --db-mode upsert ^
  --max-lotes 10 ^
  --limpar-assinaturas-apos-banco ^
  --modo-limpeza delete ^
  >> "%LOG_DIR%\rpa_assinaturas_ssrs.log" 2>&1

set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo Falha na automacao SSRS. Veja "%LOG_DIR%\rpa_assinaturas_ssrs.log"
    exit /b %EXIT_CODE%
)

echo Automacao SSRS concluida.
exit /b 0

@echo off
setlocal
cd /d %~dp0\..

REM Ajuste os caminhos abaixo conforme o servidor/PC.
set ENTRADA=C:\RPA_SSRSS_REPO\entrada
set SAIDA=C:\RPA_SSRSS_REPO\saida
set LOGS=C:\RPA_SSRSS_REPO\LOGS

REM Informe D-1 fechado no formato YYYY-MM-DD. Exemplo: 2026-06-28
set DATA_MAXIMA=2026-06-28

python etl_contact_center_pacote.py --entrada "%ENTRADA%" --saida "%SAIDA%" --logs "%LOGS%" --data-maxima %DATA_MAXIMA% --enviar-banco --db-mode upsert

pause

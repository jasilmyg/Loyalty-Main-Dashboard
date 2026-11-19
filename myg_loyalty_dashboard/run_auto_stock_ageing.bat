@echo off
rem Daily stock-ageing auto-load. Run by Windows Task Scheduler (see auto_stock_ageing.py header).
cd /d "%~dp0"
if not exist logs mkdir logs
echo ===== %date% %time% ===== >> logs\auto_stock_ageing.log
python auto_stock_ageing.py --days 5 >> logs\auto_stock_ageing.log 2>&1

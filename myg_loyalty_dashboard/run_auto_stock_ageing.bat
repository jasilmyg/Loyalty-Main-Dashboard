@echo off
rem Daily auto-loads, run by Windows Task Scheduler (backup for the cloud job):
rem   1) stock ageing report from the stock email   2) Azure sales blobs -> azure_invoice_report / azure_sales_report
cd /d "%~dp0"
if not exist logs mkdir logs
echo ===== %date% %time% ===== >> logs\auto_stock_ageing.log
python auto_stock_ageing.py --days 5 >> logs\auto_stock_ageing.log 2>&1
python auto_azure_sales.py --days 6 >> logs\auto_stock_ageing.log 2>&1

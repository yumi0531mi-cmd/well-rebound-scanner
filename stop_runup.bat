@echo off
taskkill /FI "WINDOWTITLE eq runup*" /F >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter 'Name=''pythonw.exe''' | Where-Object { $_.CommandLine -like '*runup_app.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo stopped
pause

@echo off
setlocal
cd /d "%~dp0"
"C:\Users\GAWAWA\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" app.py --port 8767
pause

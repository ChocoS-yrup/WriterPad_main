@echo off
setlocal
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
  "%LocalAppData%\Programs\Python\Python311\python.exe" "%~dp0writerpad_recovery.py" --interactive
) else (
  py -3 "%~dp0writerpad_recovery.py" --interactive
)
pause

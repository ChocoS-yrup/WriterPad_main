@echo off
setlocal
rem Python 3.14 first, without hard-coding the Python311 path. The py launcher
rem knows where Python is installed, so ask it for 3.14; only when that fails
rem fall back to the 3.14 install folders. Never run recovery on older Python.
rem Keep this file ASCII with CRLF line endings. cmd.exe reads batch files in
rem the console code page (949 on Korean Windows), and UTF-8 Korean text there
rem breaks its line parsing.
set "RECOVERY=%~dp0writerpad_recovery.py"

py -3.14 -c "pass" >nul 2>&1
if not errorlevel 1 (
  py -3.14 "%RECOVERY%" --interactive
  goto done
)

if exist "%LocalAppData%\Python\pythoncore-3.14-64\python.exe" (
  "%LocalAppData%\Python\pythoncore-3.14-64\python.exe" "%RECOVERY%" --interactive
  goto done
)

if exist "%LocalAppData%\Programs\Python\Python314\python.exe" (
  "%LocalAppData%\Programs\Python\Python314\python.exe" "%RECOVERY%" --interactive
  goto done
)

echo Python 3.14 was not found. Install Python 3.14 before running recovery.
pause
exit /b 1

:done
pause

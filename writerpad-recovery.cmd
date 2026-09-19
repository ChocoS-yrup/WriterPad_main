@echo off
setlocal
rem 3.14 로 옮긴 뒤로 Python311 경로를 하드코딩하지 않는다. py 런처가 설치
rem 위치를 알고 있으므로 먼저 그쪽에 3.14 가 있는지 물어보고, 런처 자체가
rem 없을 때만 기본 설치 경로와 py -3 순으로 물러선다.
set "RECOVERY=%~dp0writerpad_recovery.py"

py -3.14 -c "pass" >nul 2>&1
if not errorlevel 1 (
  py -3.14 "%RECOVERY%" --interactive
  goto done
)

if exist "%LocalAppData%\Programs\Python\Python314\python.exe" (
  "%LocalAppData%\Programs\Python\Python314\python.exe" "%RECOVERY%" --interactive
  goto done
)

echo Python 3.14 를 찾지 못했습니다. 설치된 다른 Python 으로 시도합니다.
py -3 "%RECOVERY%" --interactive

:done
pause

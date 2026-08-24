@echo off
cd /d "%~dp0.."
if /I "%~1"=="share" goto share
python setup.py run
set "MAESTRO_RUN_EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %MAESTRO_RUN_EXIT_CODE%

:share
rem Direct portable share mode. Secrets remain inherited from the environment.
python scripts\quick_tunnel_supervisor.py
set "MAESTRO_SHARE_EXIT_CODE=%ERRORLEVEL%"
pause
exit /b %MAESTRO_SHARE_EXIT_CODE%

@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title Dividend Dashboard V10 - DIAGNOSIS
 echo ==============================================================
echo  V10-20261103 DIAGNOSIS - THIS WINDOW WILL STAY OPEN
 echo ==============================================================
call "%~dp0scripts\diagnose_core.cmd"
echo.
echo Diagnosis saved to DIAGNOSIS_V10.txt.
:AGAIN
set "A="
set /p "A=Enter R to run again or Q to close (ENTER does nothing): "
if /I "%A%"=="R" (
 call "%~dp0scripts\diagnose_core.cmd"
 goto AGAIN
)
if /I "%A%"=="Q" exit /b 0
goto AGAIN

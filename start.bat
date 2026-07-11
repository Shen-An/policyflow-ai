@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_CMD=python"
if defined CONDA_PREFIX if exist "%CONDA_PREFIX%\python.exe" set "PYTHON_CMD=%CONDA_PREFIX%\python.exe"
if exist "E:\Coding\Anaconda\envs\policyflow\python.exe" set "PYTHON_CMD=E:\Coding\Anaconda\envs\policyflow\python.exe"

"%PYTHON_CMD%" start.py %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%

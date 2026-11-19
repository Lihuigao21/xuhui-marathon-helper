@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONDONTWRITEBYTECODE=1
set "TEMP=%~dp0.temp"
set "TMP=%TEMP%"
if not exist "%TEMP%" mkdir "%TEMP%"
if not exist "profile.json" copy /y "profile.example.json" "profile.json" >nul
set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
"%PYTHON%" -X utf8 -B "%~dp0helper.py" --profile "%~dp0profile.json" %*
set helper_exit=%ERRORLEVEL%
if not "%helper_exit%"=="0" pause
exit /b %helper_exit%

@echo off
setlocal
cd /d "%~dp0"
if not exist "profile.json" copy /y "profile.example.json" "profile.json" >nul
start "" notepad.exe "%~dp0profile.json"

@echo off
rem Nuodun Server Security Console - one-click launcher
rem Author: Nuodun Network (Tai'an) - Qiu Suyi
cd /d "%~dp0"
set "PY=%UserProfile%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=python"
start "" http://127.0.0.1:8791/
"%PY%" scripts\cli.py ui --port 8791

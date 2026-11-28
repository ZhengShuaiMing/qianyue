@echo off
rem Double-click to start Qianyue on Windows. The real work is in scripts\start\start.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start\start.ps1" %*
if errorlevel 1 pause

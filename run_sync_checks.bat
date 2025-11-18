@echo off
cd "%~dp0"
:loop
title sync checks
.venv\Scripts\python game_sync.py
timeout 1800
goto loop
pause
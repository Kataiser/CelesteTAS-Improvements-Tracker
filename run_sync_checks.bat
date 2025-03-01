@echo off
title sync checks
:loop
.venv\Scripts\python game_sync.py
timeout 1800
goto loop
pause
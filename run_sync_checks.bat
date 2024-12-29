@echo off
title sync checks
:loop
python game_sync.py
timeout 1800
goto loop
pause
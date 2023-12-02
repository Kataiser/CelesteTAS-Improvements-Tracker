@echo off
:loop
python game_sync.py
timeout 3600
goto loop
pause
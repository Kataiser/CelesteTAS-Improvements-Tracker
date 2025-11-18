@echo off
cd "%~dp0"
:loop
title bot
.venv\Scripts\python bot.py
if %ERRORLEVEL% NEQ 666 goto exit
git pull
goto loop
:exit
title dead bot
pause
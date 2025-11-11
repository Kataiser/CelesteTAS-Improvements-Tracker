@echo off
title bot
:loop
.venv\Scripts\python bot.py
if %ERRORLEVEL% NEQ 1 goto end
git pull
goto loop
:exit
title dead bot
pause
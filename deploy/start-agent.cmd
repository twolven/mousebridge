@echo off
title MouseBridge Agent (local)
cd /d C:\MouseBridge
:loop
echo [%date% %time%] Starting MouseBridge agent...
mousebridge-agent.exe
echo [%date% %time%] Agent exited (code %errorlevel%) - restarting in 3s...
timeout /t 3 /nobreak >nul
goto loop

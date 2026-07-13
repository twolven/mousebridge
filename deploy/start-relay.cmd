@echo off
title MouseBridge Relay (Breaker)
cd /d C:\MouseBridge
:loop
echo [%date% %time%] Starting MouseBridge relay (config: C:\MouseBridge\config.txt)...
mousebridge-relay.exe
echo [%date% %time%] Relay exited (code %errorlevel%) - restarting in 3s...
timeout /t 3 /nobreak >nul
goto loop

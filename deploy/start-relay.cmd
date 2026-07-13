@echo off
title MouseBridge Relay (Breaker)
cd /d C:\MouseBridge
:loop
echo [%date% %time%] Starting MouseBridge relay...
mousebridge-relay.exe --listen 0.0.0.0:8800 --forward 10.66.0.2:8800
echo [%date% %time%] Relay exited (code %errorlevel%) - restarting in 3s...
timeout /t 3 /nobreak >nul
goto loop

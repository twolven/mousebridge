@echo off
rem ===========================================================================
rem MouseBridge relay installer - run this ON BREAKER (double-click)
rem Installs to C:\MouseBridge, opens UDP 8800 in the firewall, sets the
rem relay to start at logon in a visible console window, and starts it now.
rem ===========================================================================

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator rights...
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
    exit /b
)

echo Stopping any running relay...
taskkill /IM mousebridge-relay.exe /F >nul 2>&1

echo Installing MouseBridge relay...
if not exist C:\MouseBridge mkdir C:\MouseBridge
copy /y "%~dp0mousebridge-relay.exe" C:\MouseBridge\ >nul
copy /y "%~dp0start-relay.cmd" C:\MouseBridge\ >nul

if not exist C:\MouseBridge\config.txt (
    echo Writing default config.txt...
    (
        echo # MouseBridge relay configuration
        echo LISTEN = 0.0.0.0:8800
        echo FORWARD = 10.66.0.2:8800
        echo # Process the kill hotkey terminates; blank = disabled
        echo KILL_PROCESS =
        echo KILL_KEY = backslash
        echo # Green/red bridge indicator overlay
        echo STATUS_WINDOW = on
    ) > C:\MouseBridge\config.txt
)

echo Adding firewall rule (UDP 8800 in)...
netsh advfirewall firewall delete rule name="MouseBridge Relay" >nul 2>&1
netsh advfirewall firewall add rule name="MouseBridge Relay" dir=in action=allow program="C:\MouseBridge\mousebridge-relay.exe" protocol=UDP localport=8800 >nul

echo Adding startup entry (visible console at logon)...
copy /y C:\MouseBridge\start-relay.cmd "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\MouseBridge Relay.cmd" >nul

echo Starting relay now...
start "MouseBridge Relay (Breaker)" C:\MouseBridge\start-relay.cmd

echo.
echo Done. A "MouseBridge Relay" console window should now be visible.
echo Logs also append to C:\MouseBridge\mousebridge-relay.log
echo.
pause

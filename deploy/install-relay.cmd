@echo off
rem ===========================================================================
rem MouseBridge relay installer - run this ON BREAKER (double-click)
rem Installs to %USERPROFILE%\MouseBridge, opens UDP 8800 in the firewall,
rem adds a logon startup entry, and starts the relay (tray icon + status
rem overlay; right-click the tray icon to exit).
rem ===========================================================================

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator rights...
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
    exit /b
)

set "DEST=%USERPROFILE%\MouseBridge"

echo Stopping any running relay...
taskkill /IM mousebridge-relay.exe /F >nul 2>&1

echo Installing MouseBridge relay to %DEST% ...
if not exist "%DEST%" mkdir "%DEST%"
copy /y "%~dp0mousebridge-relay.exe" "%DEST%\" >nul
copy /y "%~dp0start-relay.cmd" "%DEST%\" >nul

rem Migrate from the old C:\MouseBridge location if present
if exist C:\MouseBridge\config.txt if not exist "%DEST%\config.txt" (
    echo Migrating existing config.txt from C:\MouseBridge...
    copy /y C:\MouseBridge\config.txt "%DEST%\" >nul
)
if exist C:\MouseBridge (
    echo Removing old C:\MouseBridge...
    rmdir /s /q C:\MouseBridge
)

if not exist "%DEST%\config.txt" (
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
    ) > "%DEST%\config.txt"
)

echo Updating firewall rule (UDP 8800 in)...
netsh advfirewall firewall delete rule name="MouseBridge Relay" >nul 2>&1
netsh advfirewall firewall add rule name="MouseBridge Relay" dir=in action=allow program="%DEST%\mousebridge-relay.exe" protocol=UDP localport=8800 >nul

echo Adding startup entry...
copy /y "%DEST%\start-relay.cmd" "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\MouseBridge Relay.cmd" >nul

echo Starting relay now...
call "%DEST%\start-relay.cmd"

echo.
echo Done. Look for the status overlay (bottom-right) and the tray icon.
echo Config: %DEST%\config.txt   Log: %DEST%\mousebridge-relay.log
echo.
pause

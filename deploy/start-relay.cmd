@echo off
rem Launches the windowless relay; status overlay + tray icon are its UI.
rem Config/log: %USERPROFILE%\MouseBridge\
cd /d "%USERPROFILE%\MouseBridge"
start "" "%USERPROFILE%\MouseBridge\mousebridge-relay.exe"

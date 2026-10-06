@echo off
rem Usage: manage_open_webui.cmd start^|stop^|restart^|status
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0manage_open_webui.ps1" %1

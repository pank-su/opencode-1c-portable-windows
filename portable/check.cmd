@echo off
setlocal
for %%I in ("%~dp0.") do set "ROOT=%%~fI"
set "XDG_CONFIG_HOME=%ROOT%\userdata\.config"
set "XDG_DATA_HOME=%ROOT%\userdata\.local\share"
set "XDG_CACHE_HOME=%ROOT%\userdata\.cache"
set "XDG_STATE_HOME=%ROOT%\userdata\.local\state"
set "OPENCODE_DISABLE_AUTOUPDATE=true"

if not exist "%ROOT%\bin\opencode.exe" goto missing_binary
"%ROOT%\bin\opencode.exe" --version
if errorlevel 1 exit /b %ERRORLEVEL%
echo.
echo [OK] OpenCode is ready. Default model: opencode-go/gpt-5.6-luna
exit /b 0

:missing_binary
>&2 echo [ERROR] File not found: "%ROOT%\bin\opencode.exe"
exit /b 2

@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "ROOT=%~dp0"
set "XDG_CONFIG_HOME=%ROOT%userdata\.config"
set "XDG_DATA_HOME=%ROOT%userdata\.local\share"
set "XDG_CACHE_HOME=%ROOT%userdata\.cache"
set "XDG_STATE_HOME=%ROOT%userdata\.local\state"
set "OPENCODE_DISABLE_AUTOUPDATE=true"
set "AUTH_FILE=%XDG_DATA_HOME%\opencode\auth.json"

if not exist "%ROOT%bin\opencode.exe" goto missing_binary
if not exist "%AUTH_FILE%" goto setup_key

goto launch

:setup_key
echo OpenCode Go API key is not configured yet.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ROOT%setup-key.ps1"
if errorlevel 1 exit /b %ERRORLEVEL%

goto launch

:launch
"%ROOT%bin\opencode.exe" %*
exit /b %ERRORLEVEL%

:missing_binary
>&2 echo [ERROR] File not found: "%ROOT%bin\opencode.exe"
exit /b 2

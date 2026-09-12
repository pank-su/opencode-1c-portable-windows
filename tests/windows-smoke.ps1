$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$PortableRoot = Join-Path $RepoRoot "portable"
$TempRoot = Join-Path ([IO.Path]::GetTempPath()) ("OpenCode smoke space & (parentheses) ! ^ non-ASCII Привет {0}" -f [guid]::NewGuid().ToString("N"))

New-Item -ItemType Directory -Path $TempRoot | Out-Null
try {
    $SetupKey = Join-Path $PortableRoot "setup-key.ps1"
    $Tokens = $null
    $ParseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $SetupKey,
        [ref]$Tokens,
        [ref]$ParseErrors
    ) | Out-Null
    if ($ParseErrors.Count -ne 0) {
        throw "setup-key.ps1 failed PowerShell parsing: $($ParseErrors[0].Message)"
    }

    foreach ($ScriptName in @("opencode.cmd", "check.cmd")) {
        $ScriptPath = Join-Path $TempRoot $ScriptName
        Copy-Item -LiteralPath (Join-Path $PortableRoot $ScriptName) -Destination $ScriptPath
        $CommandLine = '""{0}""' -f $ScriptPath
        $Output = (& cmd.exe /d /c $CommandLine 2>&1 | Out-String)
        $ExitCode = $LASTEXITCODE
        if ($ExitCode -ne 2) {
            throw "$ScriptName returned $ExitCode instead of deterministic missing-binary exit 2. Output: $Output"
        }
        if ($Output -notmatch "\[ERROR\] File not found:") {
            throw "$ScriptName did not report its missing binary. Output: $Output"
        }
        if ($Output -match "OpenCode Go API key is not configured") {
            throw "$ScriptName unexpectedly attempted auth setup. Output: $Output"
        }
    }

    Push-Location -LiteralPath $TempRoot
    try {
        foreach ($ScriptName in @("opencode.cmd", "check.cmd")) {
            $Output = (& cmd.exe /d /v:on /c $ScriptName 2>&1 | Out-String)
            $ExitCode = $LASTEXITCODE
            if ($ExitCode -ne 2 -or $Output -notmatch "\[ERROR\] File not found:") {
                throw "$ScriptName failed with inherited delayed expansion. Exit: $ExitCode Output: $Output"
            }
        }
    }
    finally {
        Pop-Location
    }

    $SetupRoot = Join-Path $TempRoot "setup-junction"
    $OutsideRoot = Join-Path $TempRoot "outside-auth-target"
    New-Item -ItemType Directory -Path $SetupRoot | Out-Null
    New-Item -ItemType Directory -Path $OutsideRoot | Out-Null
    $SetupScript = Join-Path $SetupRoot "setup-key.ps1"
    Copy-Item -LiteralPath (Join-Path $PortableRoot "setup-key.ps1") -Destination $SetupScript
    New-Item -ItemType Junction -Path (Join-Path $SetupRoot "userdata") -Target $OutsideRoot | Out-Null

    $SetupOutput = (& powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $SetupScript 2>&1 | Out-String)
    $SetupExitCode = $LASTEXITCODE
    if ($SetupExitCode -eq 0 -or $SetupOutput -notmatch "reparse point") {
        throw "setup-key.ps1 did not reject an ancestor junction before prompting. Exit: $SetupExitCode Output: $SetupOutput"
    }
    if (Test-Path -LiteralPath (Join-Path $OutsideRoot ".local\share\opencode\auth.json")) {
        throw "setup-key.ps1 wrote auth.json through an ancestor junction."
    }

    $UnexpectedCommandMarker = Join-Path $TempRoot "unexpected-command.txt"
    if (Test-Path -LiteralPath $UnexpectedCommandMarker) {
        throw "A batch metacharacter triggered an unintended command."
    }
    Write-Host "Windows smoke test passed for special-character path: $TempRoot"
}
finally {
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force
    }
}

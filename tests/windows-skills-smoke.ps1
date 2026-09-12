$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$SkillsRoot = Join-Path $RepoRoot "portable\userdata\.config\opencode\skills"
$PowerShellFiles = @(Get-ChildItem -LiteralPath $SkillsRoot -Recurse -File -Filter "*.ps1")

if ($PowerShellFiles.Count -ne 71) {
    throw "Expected 71 upstream PowerShell files, found $($PowerShellFiles.Count)"
}

foreach ($File in $PowerShellFiles) {
    $Tokens = $null
    $Errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $File.FullName,
        [ref]$Tokens,
        [ref]$Errors
    ) | Out-Null
    if ($Errors.Count -gt 0) {
        $Messages = ($Errors | ForEach-Object { $_.Message }) -join "; "
        throw "PowerShell parse failure in $($File.FullName): $Messages"
    }
}

$TemporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("opencode-1c-skills-" + [guid]::NewGuid())
try {
    New-Item -ItemType Directory -Path $TemporaryRoot | Out-Null

    $PythonFiles = @(Get-ChildItem -LiteralPath $SkillsRoot -Recurse -File -Filter "*.py")
    if ($PythonFiles.Count -ne 72) {
        throw "Expected 72 upstream Python files, found $($PythonFiles.Count)"
    }
    $PythonPaths = @($PythonFiles.FullName)
    & python -c "import ast,pathlib,sys; [ast.parse(pathlib.Path(p).read_text(encoding='utf-8-sig'), filename=p) for p in sys.argv[1:]]" @PythonPaths
    if ($LASTEXITCODE -ne 0) {
        throw "Upstream Python syntax smoke failed with exit code $LASTEXITCODE"
    }

    $env:OPENCODE_1C_SKILLS_DIR = $SkillsRoot
    $InitScript = Join-Path $env:OPENCODE_1C_SKILLS_DIR "epf-init\scripts\init.ps1"
    $SourceDirectory = Join-Path $TemporaryRoot "src"

    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $InitScript `
        -Name "SmokeProcessing" `
        -Synonym "Smoke test" `
        -SrcDir $SourceDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "epf-init smoke failed with exit code $LASTEXITCODE"
    }

    $ExpectedFiles = @(
        (Join-Path $SourceDirectory "SmokeProcessing.xml"),
        (Join-Path $SourceDirectory "SmokeProcessing\Ext\ObjectModule.bsl")
    )
    foreach ($ExpectedFile in $ExpectedFiles) {
        if (-not (Test-Path -LiteralPath $ExpectedFile -PathType Leaf)) {
            throw "epf-init did not create expected file: $ExpectedFile"
        }
    }

    $DistDirectory = Join-Path $TemporaryRoot "dist"
    & python (Join-Path $RepoRoot "scripts\build_release.py") `
        --release-version "0.0.0-ci" `
        --dist $DistDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "Portable release build failed with exit code $LASTEXITCODE"
    }

    $ArchivePath = Join-Path $DistDirectory "OpenCode-1C-Portable-Windows-x64-v0.0.0-ci.zip"
    $ExtractDirectory = Join-Path $TemporaryRoot "release"
    Expand-Archive -LiteralPath $ArchivePath -DestinationPath $ExtractDirectory
    $PackageRoot = Join-Path $ExtractDirectory "OpenCode-1C-Portable-Windows-x64"
    $ConfigRoot = Join-Path $PackageRoot "userdata\.config\opencode"
    $env:XDG_CONFIG_HOME = Join-Path $PackageRoot "userdata\.config"
    $env:XDG_DATA_HOME = Join-Path $TemporaryRoot "data"
    $env:XDG_CACHE_HOME = Join-Path $TemporaryRoot "cache"
    $env:XDG_STATE_HOME = Join-Path $TemporaryRoot "state"
    $env:OPENCODE_CONFIG_DIR = $ConfigRoot
    $env:OPENCODE_1C_SKILLS_DIR = Join-Path $ConfigRoot "skills"

    $SkillJson = & (Join-Path $PackageRoot "bin\opencode.exe") debug skill
    if ($LASTEXITCODE -ne 0) {
        throw "Packaged OpenCode skill discovery failed with exit code $LASTEXITCODE"
    }
    $SkillRecords = @(($SkillJson -join [Environment]::NewLine) | ConvertFrom-Json)
    $BundledSkills = @($SkillRecords | Where-Object { $_.location -like "$ConfigRoot*" })
    if ($BundledSkills.Count -ne 79) {
        throw "Expected 79 packaged 1C skills, discovered $($BundledSkills.Count)"
    }
    foreach ($RequiredSkill in @("epf-init", "form-compile", "meta-compile", "web-test")) {
        if ($RequiredSkill -notin $BundledSkills.name) {
            throw "Packaged OpenCode did not discover required skill: $RequiredSkill"
        }
    }
}
finally {
    Remove-Item -LiteralPath $TemporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "Windows upstream 1C skills smoke test passed."
exit 0

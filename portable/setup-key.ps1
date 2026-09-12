$ErrorActionPreference = "Stop"
$Root = [IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$InstalledAuth = $false

function Test-ReparsePoint {
    param([Parameter(Mandatory = $true)][string]$Path)
    $Item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    return (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)
}

function Ensure-DirectoryPathWithoutReparse {
    param(
        [Parameter(Mandatory = $true)][string]$BasePath,
        [Parameter(Mandatory = $true)][string[]]$Components
    )

    $CurrentPath = [IO.Path]::GetFullPath($BasePath)
    $CurrentItem = Get-Item -LiteralPath $CurrentPath -Force -ErrorAction Stop
    if (-not $CurrentItem.PSIsContainer) {
        throw "Portable root must be a directory."
    }
    if (($CurrentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Portable root cannot be a reparse point."
    }

    foreach ($Component in $Components) {
        $CurrentPath = Join-Path $CurrentPath $Component
        $CurrentItem = Get-Item -LiteralPath $CurrentPath -Force -ErrorAction SilentlyContinue
        if ($null -eq $CurrentItem) {
            New-Item -ItemType Directory -Path $CurrentPath -ErrorAction Stop | Out-Null
            $CurrentItem = Get-Item -LiteralPath $CurrentPath -Force -ErrorAction Stop
        }
        if (($CurrentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Portable auth path component cannot be a reparse point: $Component"
        }
        if (-not $CurrentItem.PSIsContainer) {
            throw "Portable auth path component must be a directory: $Component"
        }
    }
    return [IO.Path]::GetFullPath($CurrentPath)
}

function Set-UserOnlyAcl {
    param([Parameter(Mandatory = $true)][string]$Path)
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $Acl = New-Object Security.AccessControl.FileSecurity
    $Acl.SetAccessRuleProtection($true, $false)
    $Acl.SetOwner([Security.Principal.NTAccount]$Identity)
    $Rule = New-Object Security.AccessControl.FileSystemAccessRule(
        $Identity,
        [Security.AccessControl.FileSystemRights]::FullControl,
        [Security.AccessControl.AccessControlType]::Allow
    )
    $Acl.SetAccessRule($Rule)
    Set-Acl -LiteralPath $Path -AclObject $Acl

    $Verified = Get-Acl -LiteralPath $Path
    $Rules = @($Verified.Access)
    if (-not $Verified.AreAccessRulesProtected -or $Rules.Count -ne 1) {
        throw "Could not secure auth.json ACL for the current user."
    }
    if ($Rules[0].IdentityReference.Value -ne $Identity -or
        $Rules[0].AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
        (($Rules[0].FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -ne [Security.AccessControl.FileSystemRights]::FullControl)) {
        throw "Could not secure auth.json ACL for the current user."
    }
}

$AuthComponents = @("userdata", ".local", "share", "opencode")
$AuthDirectory = Ensure-DirectoryPathWithoutReparse $Root $AuthComponents
$AuthPath = Join-Path $AuthDirectory "auth.json"
$TempAuthPath = Join-Path $AuthDirectory ("auth.json.{0}.tmp" -f [guid]::NewGuid().ToString("N"))

Write-Host "Enter your OpenCode Go API key. Input is hidden."
$SecureKey = Read-Host "API key" -AsSecureString
$Bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureKey)
try {
    $Key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Bstr)
    if ([string]::IsNullOrWhiteSpace($Key)) {
        throw "API key cannot be empty."
    }
    if ($Key -match "[\r\n]") {
        throw "API key must be a single line."
    }

    $VerifiedAuthDirectory = Ensure-DirectoryPathWithoutReparse $Root $AuthComponents
    if ($VerifiedAuthDirectory -ne $AuthDirectory) {
        throw "Portable auth path changed unexpectedly."
    }

    $ExistingAuth = Get-Item -LiteralPath $AuthPath -Force -ErrorAction SilentlyContinue
    if ($null -ne $ExistingAuth) {
        if (($ExistingAuth.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "auth.json cannot be a reparse point."
        }
        if ($ExistingAuth.PSIsContainer) {
            throw "auth.json must be a regular file."
        }
    }

    $Payload = @{
        "opencode-go" = @{
            type = "api"
            key = $Key
        }
    }
    $Json = $Payload | ConvertTo-Json -Depth 4
    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($TempAuthPath, $Json + [Environment]::NewLine, $Utf8NoBom)
    if (Test-ReparsePoint $TempAuthPath) {
        throw "Temporary auth.json cannot be a reparse point."
    }
    # Auth ACL hardening requires an NTFS volume; fail closed elsewhere.
    Set-UserOnlyAcl $TempAuthPath

    if ($null -ne $ExistingAuth) {
        [IO.File]::Replace($TempAuthPath, $AuthPath, $null, $true)
    } else {
        [IO.File]::Move($TempAuthPath, $AuthPath)
    }
    $InstalledAuth = $true
    if (Test-ReparsePoint $AuthPath) {
        throw "auth.json cannot be a reparse point."
    }
    Set-UserOnlyAcl $AuthPath
    Write-Host "[OK] Key saved locally to userdata."
}
catch {
    if (Test-Path -LiteralPath $TempAuthPath) {
        Remove-Item -LiteralPath $TempAuthPath -Force -ErrorAction SilentlyContinue
    }
    if ($InstalledAuth -and (Test-Path -LiteralPath $AuthPath)) {
        try {
            if (-not (Test-ReparsePoint $AuthPath)) {
                Remove-Item -LiteralPath $AuthPath -Force -ErrorAction SilentlyContinue
            }
        } catch {
            # Never follow a reparse point while cleaning up a failed write.
        }
    }
    throw
}
finally {
    if ($Bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Bstr)
    }
    $Key = $null
    $Json = $null
    $SecureKey.Dispose()
}

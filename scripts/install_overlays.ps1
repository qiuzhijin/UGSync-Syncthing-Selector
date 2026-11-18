[CmdletBinding(SupportsShouldProcess)]
param([ValidateSet('Install','Uninstall')][string]$Action = 'Install')
$ErrorActionPreference = 'Stop'
if (-not [Environment]::Is64BitProcess) { throw 'Use 64-bit PowerShell.' }
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin -and -not $WhatIfPreference) { throw 'Run this script in an Administrator PowerShell window.' }
$names = @(' 08UGSync Blue',' 08UGSync Green',' 08UGSync Red')
$legacyNames = @('UGSync Blue','UGSync Green','UGSync Red')
$ids = @('{A22F0821-2B78-49CE-B27C-1041D875B011}','{A22F0822-2B78-49CE-B27C-1041D875B011}','{A22F0823-2B78-49CE-B27C-1041D875B011}')
$overlays = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\ShellIconOverlayIdentifiers'
if ($Action -eq 'Install') {
    $manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'overlay-manifest.json') -Raw | ConvertFrom-Json
    foreach ($name in @('UGSyncOverlay.dll','blue.ico','green.ico','red.ico')) {
        $hash = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $name) -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($manifest.$name -ne $hash) { throw "Package verification failed: $name" }
    }
    $destination = Join-Path $env:ProgramFiles ('UGSyncShell\' + $manifest.'UGSyncOverlay.dll'.Substring(0,16) + '-' + $manifest.'green.ico'.Substring(0,8))
    for ($i=0; $i -lt 3; $i++) {
        $entry = Join-Path $overlays $names[$i]
        if ((Test-Path -LiteralPath $entry) -and (Get-Item -LiteralPath $entry).GetValue('') -ne $ids[$i]) { throw 'Overlay name is already used by another component.' }
        $inproc = "HKLM:\SOFTWARE\Classes\CLSID\$($ids[$i])\InprocServer32"
        if (Test-Path -LiteralPath $inproc) {
            $registered = (Get-Item -LiteralPath $inproc).GetValue('')
            if (-not $registered.StartsWith((Join-Path $env:ProgramFiles 'UGSyncShell\'),[StringComparison]::OrdinalIgnoreCase)) { throw 'COM identifier is already used by another component.' }
        }
    }
    Write-Output "Install location: $destination"
    if (-not $PSCmdlet.ShouldProcess($destination, 'Install three UGSync Explorer overlay handlers')) { return }
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    foreach ($name in @('UGSyncOverlay.dll','blue.ico','green.ico','red.ico')) {
        $target = Join-Path $destination $name
        if (Test-Path -LiteralPath $target) {
            if ((Get-FileHash -LiteralPath $target).Hash -ne (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $name)).Hash) { throw 'Existing installation differs; refusing overwrite.' }
        } else { Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $target }
    }
    for ($i=0; $i -lt 3; $i++) {
        $class = "HKLM:\SOFTWARE\Classes\CLSID\$($ids[$i])\InprocServer32"
        New-Item -Path $class -Force | Out-Null
        Set-Item -LiteralPath $class -Value (Join-Path $destination 'UGSyncOverlay.dll')
        New-ItemProperty -LiteralPath $class -Name ThreadingModel -Value Apartment -PropertyType String -Force | Out-Null
        $entry = Join-Path $overlays $names[$i]
        New-Item -Path $entry -Force | Out-Null
        Set-Item -LiteralPath $entry -Value $ids[$i]
        $legacy = Join-Path $overlays $legacyNames[$i]
        if ((Test-Path -LiteralPath $legacy) -and (Get-Item -LiteralPath $legacy).GetValue('') -eq $ids[$i]) {
            Remove-Item -LiteralPath $legacy
        }
    }
} else {
    if (-not $PSCmdlet.ShouldProcess('UGSync overlay registrations', 'Remove only the three UGSync registrations')) { return }
    for ($i=0; $i -lt 3; $i++) {
        $entry = Join-Path $overlays $names[$i]
        if (Test-Path -LiteralPath $entry) {
            if ((Get-Item -LiteralPath $entry).GetValue('') -ne $ids[$i]) { throw 'Overlay registration belongs to another component.' }
            Remove-Item -LiteralPath $entry
        }
        $legacy = Join-Path $overlays $legacyNames[$i]
        if ((Test-Path -LiteralPath $legacy) -and (Get-Item -LiteralPath $legacy).GetValue('') -eq $ids[$i]) {
            Remove-Item -LiteralPath $legacy
        }
        $class = "HKLM:\SOFTWARE\Classes\CLSID\$($ids[$i])"
        $inproc = Join-Path $class 'InprocServer32'
        if (Test-Path -LiteralPath $inproc) {
            $registered = (Get-Item -LiteralPath $inproc).GetValue('')
            if (-not $registered.StartsWith((Join-Path $env:ProgramFiles 'UGSyncShell\'),[StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected COM registration; leaving unchanged.' }
            Remove-Item -LiteralPath $inproc
            Remove-Item -LiteralPath $class
        }
    }
}
Write-Output 'Registration complete. Sign out and sign in to reload Explorer. No other overlay handlers were changed.'
Write-Output 'Program files are retained because Explorer may still have the DLL loaded.'

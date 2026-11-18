$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$vswhere = 'C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe'
$vs = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw 'Visual Studio C++ x64 tools are required' }
$output = Join-Path $project 'dist\explorer-overlays'
New-Item -ItemType Directory -Force -Path $output | Out-Null
$source = Join-Path $project 'native\overlays\overlay.cpp'
$definition = Join-Path $project 'native\overlays\overlay.def'
$vcvars = Join-Path $vs 'VC\Auxiliary\Build\vcvars64.bat'
$build = Join-Path $output 'build.cmd'
@"
@echo off
call "$vcvars"
if errorlevel 1 exit /b 1
cl /nologo /LD /MT /EHsc /std:c++17 /W4 /O2 /DUNICODE /D_UNICODE "$source" /Fo"$output\overlay.obj" /link /IMPLIB:"$output\overlay.lib" /DEF:"$definition" /OUT:"$output\UGSyncOverlay.dll" shell32.lib ole32.lib uuid.lib bcrypt.lib user32.lib
"@ | Set-Content -LiteralPath $build -Encoding ascii
& $env:ComSpec /d /c $build
if ($LASTEXITCODE -ne 0) { throw 'Overlay build failed' }
& (Join-Path $project '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'make_overlay_icons.py') $output
if ($LASTEXITCODE -ne 0) { throw 'Icon build failed' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'install_overlays.ps1') -Destination $output
$manifest = @{}
foreach ($name in @('UGSyncOverlay.dll','blue.ico','green.ico','red.ico')) {
    $manifest[$name] = (Get-FileHash -LiteralPath (Join-Path $output $name)).Hash.ToLowerInvariant()
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $output 'overlay-manifest.json') -Encoding utf8

param(
    [string]$TargetDir = '.cache/wrist-target',
    [string]$OutputDir = 'build/wrist_vision'
)
$ErrorActionPreference = 'Stop'
$workspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$env:PATH = (Join-Path $workspace '.venv/Scripts') + ';' + $env:PATH
$env:LIBCLANG_PATH = Join-Path $workspace '.venv/Lib/site-packages/clang/native'
Push-Location $workspace
try {
    cargo build --offline --locked --release --manifest-path native/wrist_vision/Cargo.toml --target-dir $TargetDir
    if ($LASTEXITCODE -ne 0) { throw 'Wrist native build failed' }
    $vendorDll = Join-Path $workspace 'third_party/openvr/win64/openvr_api.dll'
    $expectedHash = 'bab8ac6ef64e68a9ca53315b0014d131088584b2efdfa6db511d67ec03cfcb4a'
    if ((Get-FileHash -LiteralPath $vendorDll -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) { throw 'Pinned OpenVR DLL mismatch' }
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $TargetDir 'release/PuriPulyHeartWristVision.exe') -Destination $OutputDir
    Copy-Item -LiteralPath $vendorDll -Destination $OutputDir
    Copy-Item -LiteralPath (Join-Path $workspace 'third_party/openvr/LICENSE') -Destination $OutputDir
    Copy-Item -LiteralPath (Join-Path $workspace 'native/wrist_vision/NOTICE.txt') -Destination $OutputDir
    Write-Output "Wrist development component: $([IO.Path]::GetFullPath($OutputDir))"
} finally { Pop-Location }

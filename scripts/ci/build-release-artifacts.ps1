[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AppVersion,

    [Parameter(Mandatory = $true)]
    [string]$InnoSetupVersion
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-External {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter()]
        [string[]]$ArgumentList = @()
    )

    & $FilePath @ArgumentList
    $exitCode = Get-Variable -Name LASTEXITCODE -ValueOnly -ErrorAction SilentlyContinue
    if ($null -eq $exitCode) {
        $exitCode = 0
    }
    if ($exitCode -ne 0) {
        throw "Command failed with exit code ${exitCode}: $FilePath $($ArgumentList -join ' ')"
    }
}

function Invoke-ExternalProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter()]
        [string[]]$ArgumentList = @(),

        [Parameter()]
        [string]$WorkingDirectory = $PWD
    )

    $process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Command failed with exit code $($process.ExitCode): $FilePath $($ArgumentList -join ' ')"
    }
}

function Get-InnoSetupVersion {
    foreach ($registryPath in @(
        "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1",
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1"
    )) {
        if (Test-Path $registryPath) {
            return (Get-ItemProperty $registryPath).DisplayVersion
        }
    }
    return $null
}

function Resolve-ProjectEnvironmentScriptsPath {
    $projectEnvironment = $env:UV_PROJECT_ENVIRONMENT
    if ([string]::IsNullOrWhiteSpace($projectEnvironment)) {
        $projectEnvironment = ".venv"
    }
    if (-not [System.IO.Path]::IsPathRooted($projectEnvironment)) {
        $projectEnvironment = Join-Path $PWD $projectEnvironment
    }
    return Join-Path $projectEnvironment "Scripts"
}

function Resolve-ProjectEnvironmentPath {
    $scriptsPath = Resolve-ProjectEnvironmentScriptsPath
    return Split-Path -Parent $scriptsPath
}

function Resolve-CommandPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter()]
        [string[]]$Fallbacks = @()
    )

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }

    foreach ($candidate in $Fallbacks) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    throw "Required command not found on PATH: $Name"
}

$projectEnvironmentScripts = Resolve-ProjectEnvironmentScriptsPath
$projectEnvironmentPath = Resolve-ProjectEnvironmentPath
if (Test-Path $projectEnvironmentScripts) {
    $env:PATH = "$projectEnvironmentScripts;$env:PATH"
}
if ([string]::IsNullOrWhiteSpace($env:LIBCLANG_PATH)) {
    $bundledLibclangPath = Join-Path $projectEnvironmentPath "Lib\site-packages\clang\native"
    if (Test-Path $bundledLibclangPath) {
        $env:LIBCLANG_PATH = $bundledLibclangPath
    }
}

$cargoCommand = Resolve-CommandPath -Name "cargo" -Fallbacks @(
    (Join-Path $env:USERPROFILE ".cargo\bin\cargo.exe")
)
$cmakeCommand = Resolve-CommandPath -Name "cmake" -Fallbacks @(
    (Join-Path $projectEnvironmentScripts "cmake.exe"),
    "C:\Program Files\CMake\bin\cmake.exe"
)
$uvCommand = Resolve-CommandPath -Name "uv" -Fallbacks @(
    (Join-Path $env:USERPROFILE ".local\bin\uv.exe")
)
$cmakeCommandDirectory = Split-Path -Parent $cmakeCommand
if (-not [string]::IsNullOrWhiteSpace($cmakeCommandDirectory)) {
    $env:PATH = "$cmakeCommandDirectory;$env:PATH"
}
$env:CMAKE = $cmakeCommand

$overlayManifestPath = Join-Path $PWD "native/overlay/Cargo.toml"
$overlayBuildDir = Join-Path $PWD "build/overlay"
$overlayReleasePath = Join-Path $PWD "native/overlay/target/release/PuriPulyHeartOverlay.exe"
$overlayStagedPath = Join-Path $overlayBuildDir "PuriPulyHeartOverlay.exe"

Write-Host "Building Rust overlay executable..."
Invoke-External -FilePath $cargoCommand -ArgumentList @(
    "build",
    "--manifest-path",
    $overlayManifestPath,
    "--locked",
    "--release",
    "--bin",
    "PuriPulyHeartOverlay",
    "--target-dir",
    (Join-Path $PWD "native/overlay/target")
)

if (-not (Test-Path $overlayReleasePath)) {
    throw "Rust overlay executable not found: $overlayReleasePath"
}

New-Item -ItemType Directory -Force -Path $overlayBuildDir | Out-Null
Copy-Item -Path $overlayReleasePath -Destination $overlayStagedPath -Force

if (-not (Test-Path $overlayStagedPath)) {
    throw "Staged overlay executable not found: $overlayStagedPath"
}

Write-Host "Smoke-testing staged overlay executable..."
Invoke-External -FilePath $overlayStagedPath -ArgumentList @("--check-startup-contract")

Write-Host "Building Windows executable..."
Invoke-External -FilePath $uvCommand -ArgumentList @("run", "python", "-m", "PyInstaller", "--noconfirm", "build.spec")

$exePath = Join-Path $PWD "dist/PuriPulyHeart/PuriPulyHeart.exe"
if (-not (Test-Path $exePath)) {
    throw "Packaged executable not found: $exePath"
}

$packagedOverlayPath = Join-Path $PWD "dist/PuriPulyHeart/PuriPulyHeartOverlay.exe"
Copy-Item -Path $overlayStagedPath -Destination $packagedOverlayPath -Force

if (-not (Test-Path $packagedOverlayPath)) {
    throw "Packaged overlay executable not found: $packagedOverlayPath"
}

Write-Host "Smoke-testing packaged executable..."
$smokeTest = Start-Process -FilePath $exePath -ArgumentList @("osc-send", "ci-smoke") -Wait -PassThru
if ($smokeTest.ExitCode -ne 0) {
    throw "Packaged executable smoke test failed with exit code $($smokeTest.ExitCode)"
}

Write-Host "Smoke-testing packaged overlay executable..."
Invoke-External -FilePath $packagedOverlayPath -ArgumentList @("--check-startup-contract")

$isccPath = Join-Path ([Environment]::GetFolderPath("ProgramFilesX86")) "Inno Setup 6\ISCC.exe"
$currentInnoVersion = Get-InnoSetupVersion

if ($currentInnoVersion -eq $InnoSetupVersion -and (Test-Path $isccPath)) {
    Write-Host "Using installed Inno Setup $currentInnoVersion."
} else {
    $choco = Get-Command choco -ErrorAction SilentlyContinue
    if ($null -eq $choco) {
        throw "Chocolatey is required to install Inno Setup $InnoSetupVersion."
    }

    Write-Host "Installing Inno Setup $InnoSetupVersion..."
    Invoke-External -FilePath $choco.Source -ArgumentList @(
        "install",
        "innosetup",
        "--version=$InnoSetupVersion",
        "--no-progress",
        "-y"
    )

    $currentInnoVersion = Get-InnoSetupVersion
}

if (-not (Test-Path $isccPath)) {
    throw "ISCC.exe not found after Inno Setup install: $isccPath"
}

if ($currentInnoVersion -ne $InnoSetupVersion) {
    throw "Inno Setup version mismatch: expected $InnoSetupVersion, found $currentInnoVersion"
}

$installerPath = Join-Path $PWD "installer_output/PuriPulyHeart-Setup-$AppVersion.exe"
$installerHashPath = "$installerPath.sha256"
if (Test-Path $installerPath) {
    Remove-Item -Path $installerPath -Force
}
if (Test-Path $installerHashPath) {
    Remove-Item -Path $installerHashPath -Force
}

Write-Host "Building installer..."
Invoke-ExternalProcess -FilePath $isccPath -ArgumentList @("installer.iss") -WorkingDirectory $PWD

if (-not (Test-Path $installerPath)) {
    throw "Installer not found: $installerPath"
}

if (-not (Test-Path $packagedOverlayPath)) {
    Copy-Item -Path $overlayStagedPath -Destination $packagedOverlayPath -Force
}

if (-not (Test-Path $packagedOverlayPath)) {
    throw "Packaged overlay executable not found after installer build: $packagedOverlayPath"
}

Write-Host "Generating SHA256..."
$hash = (Get-FileHash -Path $installerPath -Algorithm SHA256).Hash
"$hash  PuriPulyHeart-Setup-$AppVersion.exe" | Out-File -FilePath $installerHashPath -Encoding ascii

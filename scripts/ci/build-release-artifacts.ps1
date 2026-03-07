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

    $process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -Wait -PassThru
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

Write-Host "Building Windows executable..."
Invoke-External -FilePath "uv" -ArgumentList @("run", "python", "-m", "PyInstaller", "--noconfirm", "build.spec")

$exePath = Join-Path $PWD "dist/PuriPulyHeart/PuriPulyHeart.exe"
if (-not (Test-Path $exePath)) {
    throw "Packaged executable not found: $exePath"
}

Write-Host "Smoke-testing packaged executable..."
$smokeTest = Start-Process -FilePath $exePath -ArgumentList @("osc-send", "ci-smoke") -Wait -PassThru
if ($smokeTest.ExitCode -ne 0) {
    throw "Packaged executable smoke test failed with exit code $($smokeTest.ExitCode)"
}

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

Write-Host "Building installer..."
Invoke-External -FilePath $isccPath -ArgumentList @("installer.iss")

$installerPath = Join-Path $PWD "installer_output/PuriPulyHeart-Setup-$AppVersion.exe"
if (-not (Test-Path $installerPath)) {
    throw "Installer not found: $installerPath"
}

Write-Host "Generating SHA256..."
$hash = (Get-FileHash -Path $installerPath -Algorithm SHA256).Hash
"$hash  PuriPulyHeart-Setup-$AppVersion.exe" | Out-File -FilePath "$installerPath.sha256" -Encoding ascii

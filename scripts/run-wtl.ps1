<#
.SYNOPSIS
    Launch wow.tools.local (WTL) from the correct working directory.

.DESCRIPTION
    IMPORTANT: Close WoW and idle (or close) Battle.net before running this.
    Both hold locks on the CASC data files, and WTL will either fail to load
    the build or load it partially, with no clear error.

    WTL must run with vendor/wow.tools.local as the working directory for two
    reasons:
      * Program.cs exits immediately if 'wwwroot' is not in the current
        directory.
      * SettingsManager reads config.json from the current directory first,
        falling back to the executable directory. Launching from anywhere else
        silently picks up defaults instead of our config (wrong product,
        wrong region, no DBD directory).

    This script exists so that trap cannot be hit by accident.

    WTL is a blocking server. It holds the terminal until stopped with Ctrl+C.
    First start downloads definitions and the listfile and indexes the build:
    expect several minutes and 3-5 GB of RAM.
#>

[CmdletBinding()]
param(
    [string] $Configuration = 'Release'
)

$ErrorActionPreference = 'Stop'

$wtlDir = Join-Path (Split-Path -Parent $PSScriptRoot) 'vendor/wow.tools.local'

if (-not (Test-Path (Join-Path $wtlDir 'wwwroot'))) {
    throw "WTL not found at $wtlDir (no wwwroot). Clone it into vendor/ first."
}
if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw 'dotnet not found on PATH. The .NET 10 SDK is required to build WTL.'
}
if (-not (Test-Path (Join-Path $wtlDir 'config.json'))) {
    Write-Warning 'config.json not found in the WTL directory; WTL will fall back to defaults.'
}

Write-Host ''
Write-Host 'Starting wow.tools.local'
Write-Host '  Close WoW and idle Battle.net first, or the CASC load will fail.'
Write-Host ''
Write-Host '  http://localhost:5080'
Write-Host ''
Write-Host '  Ctrl+C to stop.'
Write-Host ''

Push-Location $wtlDir
try {
    dotnet run -c $Configuration
}
finally {
    Pop-Location
}

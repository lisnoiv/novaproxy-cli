# NovaProxy CLI - one-line installer for Windows (PowerShell)
# Run: irm https://raw.githubusercontent.com/lisnoiv/novaproxy-cli/main/install.ps1 | iex
$ErrorActionPreference = 'Stop'

$Repo = 'https://github.com/lisnoiv/novaproxy-cli.git'
$Dir  = Join-Path $env:USERPROFILE 'novaproxy-cli'
$Bin  = Join-Path $env:USERPROFILE 'bin'
$Exe  = 'novaproxy.exe'

Write-Host '==> NovaProxy CLI installer'

# git
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host '==> git not found, installing via winget...'
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id Git.Git -e --source winget --accept-source-agreements --accept-package-agreements
        $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('Path','User')
    } else {
        Write-Host 'ERROR: install git manually: https://git-scm.com/download/win'
        exit 1
    }
}

# Go
if (-not (Get-Command go -ErrorAction SilentlyContinue)) {
    Write-Host '==> Go toolchain not found, installing via winget...'
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install --id GoLang.Go -e --source winget --accept-source-agreements --accept-package-agreements
        $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('Path','User')
    } else {
        Write-Host 'ERROR: install Go manually: https://go.dev/dl/'
        exit 1
    }
}

if (Test-Path (Join-Path $Dir '.git')) {
    Write-Host "==> Updating $Dir"
    git -C $Dir pull --ff-only
} else {
    Write-Host "==> Cloning $Repo"
    git clone --depth 1 $Repo $Dir
}

Set-Location $Dir
Write-Host '==> Building (vendored deps, no downloads)...'
$env:GOFLAGS = '-mod=vendor'
go build -trimpath -ldflags '-s -w' -o $Exe .

New-Item -ItemType Directory -Force -Path $Bin | Out-Null
Copy-Item $Exe (Join-Path $Bin $Exe) -Force

# add to user PATH if missing
$userPath = [System.Environment]::GetEnvironmentVariable('Path','User')
if ($userPath -notlike "*$Bin*") {
    [System.Environment]::SetEnvironmentVariable('Path', "$userPath;$Bin", 'User')
    Write-Host "==> Added $Bin to user PATH (restart terminal)"
}
$env:Path = "$env:Path;$Bin"

Write-Host ''
Write-Host "==> Installed: $Bin\$Exe"
& (Join-Path $Bin $Exe) --version
Write-Host ''
Write-Host 'Zapusti:  novaproxy'
Write-Host 'Pri pervom zapuske vvedi klyuch vida px_live_...'

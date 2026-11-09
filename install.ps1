# NovaProxy CLI - one-line installer for Windows PowerShell.
#   irm https://raw.githubusercontent.com/lisnoiv/novaproxy-cli/main/install.ps1 | iex
$ErrorActionPreference = 'Stop'

function Say($m) { Write-Host "[novaproxy] $m" -ForegroundColor Magenta }
function Err($m) { Write-Host "[novaproxy] $m" -ForegroundColor Red }

$repo = 'https://github.com/lisnoiv/novaproxy-cli.git'
$dest = Join-Path $env:USERPROFILE '.novaproxy\app'
$binDir = Join-Path $env:USERPROFILE '.novaproxy\bin'

# --- python -----------------------------------------------------------------
$py = $null
foreach ($cand in @('python', 'python3', 'py')) {
    try {
        $v = & $cand -c "import sys; print(sys.version_info[0]*10+sys.version_info[1])" 2>$null
        if ([int]$v -ge 38) { $py = $cand; break }
    } catch { }
}
if (-not $py) {
    Say 'Python не найден. Устанавливаю через winget...'
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
        $py = 'python'
        $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('Path','User')
    } else {
        Err 'Установите Python 3.8+ с https://python.org и перезапустите установку.'
        exit 1
    }
}
Say "Python: $(& $py --version 2>&1)"

# --- git --------------------------------------------------------------------
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Say 'git не найден. Устанавливаю через winget...'
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install -e --id Git.Git --silent --accept-package-agreements --accept-source-agreements
        $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('Path','User')
    } else {
        Err 'Установите git с https://git-scm.com и перезапустите установку.'
        exit 1
    }
}

# --- sources -----------------------------------------------------------------
New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
if (Test-Path (Join-Path $dest '.git')) {
    Say 'Обновляю существующую установку...'
    git -C $dest pull --ff-only -q
} else {
    if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
    Say 'Скачиваю NovaProxy CLI...'
    git clone -q $repo $dest
}

# --- deps ---------------------------------------------------------------------
Say 'Устанавливаю зависимости...'
& $py -m pip install --quiet --upgrade pip | Out-Null
& $py -m pip install --quiet --user -e $dest
& $py -m pip install --quiet --user pyreadline3 2>$null | Out-Null

# --- launcher -------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
$launcher = Join-Path $binDir 'novaproxy.cmd'
@"
@echo off
$py -m novaproxy %*
"@ | Set-Content -Encoding ASCII $launcher

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath -notlike "*$binDir*") {
    [Environment]::SetEnvironmentVariable('Path', "$userPath;$binDir", 'User')
    $env:Path += ";$binDir"
    Say "Добавлено в PATH: $binDir (перезапустите терминал, если команда не находится)"
}

Say 'Готово! Запустите:  novaproxy'
Say 'При первом запуске введите ключ формата px_live_...'

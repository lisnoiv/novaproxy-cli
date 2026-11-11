# NovaProxy CLI - one-line installer for Windows (PowerShell)
# Run: irm https://raw.githubusercontent.com/lisnoiv/novaproxy-cli/main/install.ps1 | iex
# Safe to run in elevated (Admin) PowerShell. No winget needed.

$Repo    = 'https://github.com/lisnoiv/novaproxy-cli'
$ZipUrl  = "$Repo/archive/refs/heads/main.zip"
$Dir     = Join-Path $env:USERPROFILE 'novaproxy-cli'
$Bin     = Join-Path $env:USERPROFILE 'bin'
$Exe     = 'novaproxy.exe'
$GoVer   = '1.22.5'
$GoMsi64 = "https://dl.google.com/go/go$GoVer.windows-amd64.msi"

function Say($msg) { Write-Host $msg }
function Fail($msg) {
    Write-Host ''
    Write-Host "ERROR: $msg" -ForegroundColor Red
    Write-Host ''
    Read-Host 'Nazhmi Enter dlya vyhoda'
    exit 1
}

# keep window open on any unexpected error
trap {
    Write-Host ''
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ''
    Read-Host 'Nazhmi Enter dlya vyhoda'
    exit 1
}

Say '==> NovaProxy CLI installer'
Say ''

# --- Go toolchain ---------------------------------------------------------
$goCmd = Get-Command go -ErrorAction SilentlyContinue
if (-not $goCmd) {
    # maybe installed but not in this session PATH yet
    $defaultGo = 'C:\Program Files\Go\bin\go.exe'
    if (Test-Path $defaultGo) {
        $env:Path = "C:\Program Files\Go\bin;$env:Path"
        $goCmd = $true
    }
}
if (-not $goCmd) {
    Say '==> Go ne nayden. Skachivayu ustanovshchik (63 MB)...'
    $msi = Join-Path $env:TEMP 'go_installer.msi'
    $ok = $false
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $wc = New-Object System.Net.WebClient
        $wc.Headers.Add('User-Agent', 'novaproxy-installer')
        $wc.DownloadFileAsync((New-Object System.Uri($GoMsi64)), $msi)
        $total = 63184896.0
        while ($wc.IsBusy) {
            if (Test-Path $msi) {
                $cur = (Get-Item $msi).Length
                $pct = [math]::Min(99, [int]($cur / $total * 100))
                $mb = [math]::Round($cur / 1MB)
                Write-Host ("`r  Skachano: {0} MB / 63 MB  ({1}%)   " -f $mb, $pct) -NoNewline
            }
            Start-Sleep -Milliseconds 400
        }
        Write-Host ''
        $ok = (Test-Path $msi) -and ((Get-Item $msi).Length -gt 60000000)
    } catch { $ok = $false }
    if (-not $ok) {
        try {
            Invoke-WebRequest -Uri $GoMsi64 -OutFile $msi -UseBasicParsing
            $ok = (Test-Path $msi) -and ((Get-Item $msi).Length -gt 60000000)
        } catch { $ok = $false }
    }
    if (-not $ok) { Fail "Ne udalos skachat Go. Ustanovi vruchnuyu: https://go.dev/dl/ i zapusti snova." }

    Say '==> Ustanavlivayu Go tikhim rezhimom (1-2 min, nichego ne nazhimay)...'
    $p = Start-Process msiexec.exe -ArgumentList "/i `"$msi`" /qn /norestart" -Wait -PassThru
    Remove-Item $msi -Force -ErrorAction SilentlyContinue
    if ($p.ExitCode -ne 0) { Fail "Ustanovka Go zavershilas s kodom $($p.ExitCode). Ustanovi vruchnuyu: https://go.dev/dl/" }

    # refresh PATH from registry (machine + user)
    $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
                [System.Environment]::GetEnvironmentVariable('Path','User')
    if (-not (Get-Command go -ErrorAction SilentlyContinue)) {
        $defaultGo = 'C:\Program Files\Go\bin\go.exe'
        if (Test-Path $defaultGo) { $env:Path = "C:\Program Files\Go\bin;$env:Path" }
        else { Fail 'Go ustanovlen, no ne vizden v PATH. Perezapusti PowerShell i povtori.' }
    }
}
Say ("==> Go: " + (go version))

# --- source code (zip, no git needed) -------------------------------------
if (Test-Path (Join-Path $Dir 'main.go')) {
    Say "==> Papka $Dir uzhe est, obnovlyayu..."
    Remove-Item $Dir -Recurse -Force
}
Say '==> Skachivayu ishodniki...'
$zip = Join-Path $env:TEMP 'novaproxy-cli.zip'
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $ZipUrl -OutFile $zip -UseBasicParsing
} catch {
    Fail "Ne udalos skachat repozitoriy. Prover internet i zapusti snova."
}
Expand-Archive -Path $zip -DestinationPath $env:TEMP -Force
Remove-Item $zip -Force
$extracted = Join-Path $env:TEMP 'novaproxy-cli-main'
if (-not (Test-Path $extracted)) { Fail 'Arhiv raspakovalsya neozhidanno.' }
Move-Item $extracted $Dir -Force

# --- build ------------------------------------------------------------------
Set-Location $Dir
Say '==> Sobirayu (vendored deps, nichego ne kachaetsya)...'
$env:GOFLAGS = '-mod=vendor'
& go build -trimpath -ldflags '-s -w' -o $Exe .
if ($LASTEXITCODE -ne 0) { Fail 'Sborka ne udalas.' }

# --- install ----------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
Copy-Item $Exe (Join-Path $Bin $Exe) -Force

# add to user PATH if missing
$userPath = [System.Environment]::GetEnvironmentVariable('Path','User')
if ($userPath -notlike "*$Bin*") {
    [System.Environment]::SetEnvironmentVariable('Path', ($userPath.TrimEnd(';') + ';' + $Bin), 'User')
    Say "==> Dobavil $Bin v PATH"
}
$env:Path = "$env:Path;$Bin"

Say ''
Say "==> Ustanovleno: $Bin\$Exe"
& (Join-Path $Bin $Exe) --version
Say ''
Say 'Zapusti:  novaproxy'
Say 'Pri pervom zapuske vvedi klyuch vida px_live_...'
Say ''
Read-Host 'Nazhmi Enter dlya vyhoda'

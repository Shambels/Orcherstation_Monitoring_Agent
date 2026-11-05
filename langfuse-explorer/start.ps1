<#
=============================================================================
 Langfuse Explorer : démarrage en une commande (Windows PowerShell 5.1+ / 7)

   .\start.ps1            génère .env si besoin, démarre Langfuse, attend, injecte le seed
   .\start.ps1 -Reset     repart de zéro (supprime les données du kit, PAS celles du cours)
   .\start.ps1 -NoSeed    démarre sans injecter les données
   .\start.ps1 -Stop      arrête le kit (les données sont conservées)

 Si l'exécution des scripts est bloquée :
   powershell -ExecutionPolicy Bypass -File .\start.ps1
=============================================================================
#>
param(
    [switch]$Reset,
    [switch]$NoSeed,
    [switch]$Stop
)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

function Fail($msg) { Write-Host "X $msg" -ForegroundColor Red; exit 1 }

if ($Stop) { docker compose stop; Write-Host "Kit arrete (donnees conservees)."; exit 0 }

Write-Host "== 1/4 Prerequis" -ForegroundColor Cyan
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail "Docker absent : installer Docker Desktop." }
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail "Docker ne repond pas : demarrer Docker Desktop puis relancer." }
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Fail "uv absent : powershell -ExecutionPolicy ByPass -c `"irm https://astral.sh/uv/install.ps1 | iex`""
}

Write-Host "== 2/4 Configuration" -ForegroundColor Cyan
uv run --project seed generate_env.py
if ($LASTEXITCODE -ne 0) { Fail "generate_env.py a echoue." }

$envFile = Get-Content .env | Where-Object { $_ -match "^[A-Z_]+=" }
$cfg = @{}
foreach ($line in $envFile) { $k, $v = $line -split "=", 2; $cfg[$k] = $v }
$port = if ($cfg["EXPLORER_PORT"]) { $cfg["EXPLORER_PORT"] } else { "3100" }

$busy = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
$ours = docker compose ps --status running 2>$null | Select-String "langfuse-web"
if ($busy -and -not $ours) { Fail "Le port $port est deja pris. Changer EXPLORER_PORT et LANGFUSE_BASE_URL dans .env." }

if ($Reset) {
    Write-Host "-Reset : suppression des volumes du projet 'langfuse-explorer' uniquement." -ForegroundColor Yellow
    docker compose down -v
}

Write-Host "== 3/4 Demarrage de Langfuse (premier lancement : 2 a 4 min)" -ForegroundColor Cyan
docker compose up -d
if ($LASTEXITCODE -ne 0) { Fail "docker compose up a echoue." }

$ok = $false
for ($i = 1; $i -le 90; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:$port/api/public/health" -UseBasicParsing -TimeoutSec 3
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch { }
    Write-Host -NoNewline "."
    Start-Sleep -Seconds 4
}
Write-Host ""
if (-not $ok) { Fail "Langfuse ne repond pas apres 6 min : docker compose logs langfuse-web" }
Write-Host "Langfuse repond." -ForegroundColor Green

if (-not $NoSeed) {
    Write-Host "== 4/4 Injection des donnees de demonstration" -ForegroundColor Cyan
    uv run --project seed seed/seed_langfuse.py
    if ($LASTEXITCODE -ne 0) { Fail "Le seed a echoue (voir le message ci-dessus)." }
}

Write-Host ""
Write-Host "------------------------------------------------------------" -ForegroundColor Green
Write-Host "  Langfuse Explorer est pret"
Write-Host "  UI           : http://localhost:$port"
Write-Host "  Login        : explorer@helpdesk.local"
Write-Host "  Mot de passe : $($cfg['LANGFUSE_INIT_USER_PASSWORD'])"
Write-Host "  Guide        : guide\index.html"
Write-Host "------------------------------------------------------------" -ForegroundColor Green
Start-Process "guide\index.html"

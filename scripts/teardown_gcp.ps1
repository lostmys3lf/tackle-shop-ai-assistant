<#
.SYNOPSIS
Backup database dan workflow n8n dari VM GCP ke backups/, restore ke Postgres lokal, lalu hapus resource GCP setelah konfirmasi.
#>
[CmdletBinding()]
param(
    [string]$Project = "neat-geode-514006-g4",
    [string]$Zone = "asia-southeast2-a",
    [string]$Region = "asia-southeast2",
    [string]$VmName = "tackle-shop",
    [string]$AddressName = "tackle-shop-ip",
    [string]$NetworkName = "tackle-shop-vpc",
    [string]$SubnetName = "tackle-shop-subnet",
    [string[]]$FirewallRules = @("tackle-shop-allow-web", "tackle-shop-allow-iap-ssh"),
    [string]$RemoteDir = "/opt/tackle-shop"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LocalEnvPath = Join-Path $RepoRoot ".env"

if (-not (Test-Path $LocalEnvPath)) {
    throw "File .env lokal tidak ditemukan di $LocalEnvPath."
}

function Invoke-RemoteBash {
    param([string]$ScriptText)
    $lfScript = ($ScriptText -replace "`r`n", "`n").Trim() + "`n"
    $b64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($lfScript))
    $RemoteCmd = "f=`$(mktemp); echo $b64 | base64 -d > `"`$f`"; bash -euo pipefail `"`$f`"; rc=`$?; rm -f `"`$f`"; exit `$rc"
    & gcloud compute ssh $VmName --project=$Project --zone=$Zone --tunnel-through-iap --quiet --command=$RemoteCmd
    if ($LASTEXITCODE -ne 0) {
        throw "Perintah SSH di VM gagal dengan exit code $LASTEXITCODE."
    }
}

$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$BackupDir = Join-Path $RepoRoot (Join-Path "backups" $Timestamp)
New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null

$LocalSqlPath = Join-Path $BackupDir "postgres_dump.sql"
$LocalWfPath = Join-Path $BackupDir "n8n_workflows.json"

Write-Host "[1/5] Membuat pg_dump dan ekspor workflow n8n di VM..."
$BackupScript = @"
cd "$RemoteDir"
set -a
. ./.env
set +a
sudo docker compose -f docker-compose.prod.yml exec -T postgres pg_dump -U "`$POSTGRES_USER" -d "`$POSTGRES_DB" --clean --if-exists > /tmp/tackle_shop_dump.sql
sudo docker compose -f docker-compose.prod.yml exec -T n8n n8n export:workflow --all --output=/tmp/n8n_workflows.json
sudo docker cp tackle-n8n:/tmp/n8n_workflows.json /tmp/n8n_workflows.json
sudo docker compose -f docker-compose.prod.yml exec -u root -T n8n rm -f /tmp/n8n_workflows.json
chmod 644 /tmp/tackle_shop_dump.sql /tmp/n8n_workflows.json
"@
Invoke-RemoteBash -ScriptText $BackupScript

Write-Host "[2/5] Mengunduh backup ke $BackupDir..."
& gcloud compute scp --tunnel-through-iap --quiet --project=$Project --zone=$Zone "${VmName}:/tmp/tackle_shop_dump.sql" "$LocalSqlPath"
if ($LASTEXITCODE -ne 0) { throw "Gagal mengunduh postgres_dump.sql dari VM." }
& gcloud compute scp --tunnel-through-iap --quiet --project=$Project --zone=$Zone "${VmName}:/tmp/n8n_workflows.json" "$LocalWfPath"
if ($LASTEXITCODE -ne 0) { throw "Gagal mengunduh n8n_workflows.json dari VM." }

Invoke-RemoteBash -ScriptText "rm -f /tmp/tackle_shop_dump.sql /tmp/n8n_workflows.json"

if ((Get-Item $LocalSqlPath).Length -le 0 -or (Get-Item $LocalWfPath).Length -le 0) {
    throw "File backup kosong; proses teardown dihentikan sebelum menghapus resource GCP."
}

Write-Host "[3/5] Merestore dump database ke Postgres lokal..."
$envMap = @{}
foreach ($line in (Get-Content -Path $LocalEnvPath -Encoding UTF8)) {
    if ($line -match "^([A-Za-z_][A-Za-z0-9_]*)=(.*)$") {
        $envMap[$Matches[1]] = $Matches[2]
    }
}
$PgUser = $envMap["POSTGRES_USER"]
$PgDb = $envMap["POSTGRES_DB"]
if ([string]::IsNullOrWhiteSpace($PgUser) -or [string]::IsNullOrWhiteSpace($PgDb)) {
    throw "POSTGRES_USER atau POSTGRES_DB tidak ditemukan di .env lokal."
}

& docker compose up -d postgres
if ($LASTEXITCODE -ne 0) { throw "Gagal menjalankan container postgres lokal." }

for ($i = 0; $i -lt 30; $i++) {
    & docker compose exec -T postgres pg_isready -U $PgUser -d $PgDb >$null 2>&1
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 2
}

Get-Content -Path $LocalSqlPath -Raw -Encoding UTF8 | Out-Null
$RestoreCmd = "docker compose exec -T postgres psql -U `"$PgUser`" -d `"$PgDb`" -v ON_ERROR_STOP=1 < `"$LocalSqlPath`""
& cmd /c $RestoreCmd
if ($LASTEXITCODE -ne 0) {
    throw "Restore ke Postgres lokal gagal. Resource GCP TIDAK dihapus."
}

Write-Host "[4/5] Backup dan restore lokal selesai."
Write-Host "Lokasi backup: $BackupDir"
$Confirm = Read-Host 'Ketik "HAPUS" (huruf kapital) untuk menghapus VM, disk, IP statis, firewall, dan VPC di GCP'
if ($Confirm -cne "HAPUS") {
    Write-Host "Konfirmasi dibatalkan. Tidak ada resource GCP yang dihapus."
    exit 0
}

Write-Host "[5/5] Menghapus resource GCP..."
& gcloud compute instances delete $VmName --project=$Project --zone=$Zone --delete-disks=all --quiet
& gcloud compute addresses delete $AddressName --project=$Project --region=$Region --quiet
foreach ($rule in $FirewallRules) {
    & gcloud compute firewall-rules delete $rule --project=$Project --quiet
}
& gcloud compute networks subnets delete $SubnetName --project=$Project --region=$Region --quiet
& gcloud compute networks delete $NetworkName --project=$Project --quiet

Write-Host "Teardown GCP selesai. Untuk menjalankan bot secara lokal, klik start.bat."

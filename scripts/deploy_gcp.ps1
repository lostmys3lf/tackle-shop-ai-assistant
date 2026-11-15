<#
.SYNOPSIS
Deploy idempoten Asisten Toko Pancing Papa ke VM GCP Compute Engine.
#>
[CmdletBinding()]
param(
    [string]$Project = "neat-geode-514006-g4",
    [string]$Zone = "asia-southeast2-a",
    [string]$Region = "asia-southeast2",
    [string]$VmName = "tackle-shop",
    [string]$AddressName = "tackle-shop-ip",
    [string]$RepoUrl = "https://github.com/lostmys3lf/tackle-shop-ai-assistant.git",
    [string]$Branch = "main",
    [string]$RemoteDir = "/opt/tackle-shop",
    [string]$OcrProvider = "groq_vision",
    [switch]$SkipWorkflowSync
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

Write-Host "[1/6] Mengambil IP statis VM ($AddressName)..."
$StaticIp = (& gcloud compute addresses describe $AddressName --project=$Project --region=$Region --format="get(address)").Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($StaticIp)) {
    throw "Gagal mengambil IP statis $AddressName di $Region."
}
$SslipHost = ($StaticIp -replace "\.", "-") + ".sslip.io"
$WebhookUrl = "https://$SslipHost/"
Write-Host "Host VM: $SslipHost"

Write-Host "[2/6] Menyiapkan Docker, Git, dan sinkronisasi repository di VM..."
$SyncScript = @"
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl git gnupg
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=`$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu `$(. /etc/os-release && echo "`$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo usermod -aG docker "`$USER" || true
fi
if ! command -v git >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y git
fi
sudo mkdir -p "$RemoteDir"
sudo chown "`$USER:`$USER" "$RemoteDir"
if [ ! -d "$RemoteDir/.git" ]; then
  git clone --branch "$Branch" "$RepoUrl" "$RemoteDir"
else
  git -C "$RemoteDir" fetch origin "$Branch"
  git -C "$RemoteDir" checkout "$Branch"
  git -C "$RemoteDir" reset --hard "origin/$Branch"
fi
"@
Invoke-RemoteBash -ScriptText $SyncScript

Write-Host "[3/6] Menyalin .env produksi ke VM tanpa mencetak rahasia..."
$TempEnvPath = Join-Path ([System.IO.Path]::GetTempPath()) ("tackle-vm-env-" + [System.Guid]::NewGuid().ToString("N") + ".env")
try {
    $rawLines = Get-Content -Path $LocalEnvPath -Encoding UTF8
    $overrides = @{
        "WEBHOOK_URL"  = $WebhookUrl
        "SSLIP_HOST"   = $SslipHost
        "OCR_PROVIDER" = $OcrProvider
    }
    $seen = @{}
    $outLines = New-Object System.Collections.Generic.List[string]
    foreach ($line in $rawLines) {
        $matched = $false
        foreach ($key in $overrides.Keys) {
            if ($line -match "^${key}=") {
                $outLines.Add("${key}=$($overrides[$key])")
                $seen[$key] = $true
                $matched = $true
                break
            }
        }
        if (-not $matched) {
            $outLines.Add($line)
        }
    }
    foreach ($key in $overrides.Keys) {
        if (-not $seen.ContainsKey($key)) {
            $outLines.Add("${key}=$($overrides[$key])")
        }
    }
    [System.IO.File]::WriteAllLines($TempEnvPath, $outLines, [System.Text.UTF8Encoding]::new($false))

    & gcloud compute scp --tunnel-through-iap --quiet --project=$Project --zone=$Zone "$TempEnvPath" "${VmName}:${RemoteDir}/.env"
    if ($LASTEXITCODE -ne 0) {
        throw "Gagal menyalin .env ke VM."
    }
}
finally {
    if (Test-Path $TempEnvPath) {
        Remove-Item -Path $TempEnvPath -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "[4/6] Menjalankan docker compose produksi, migrasi Alembic, dan seed idempoten..."
$DeployScript = @"
cd "$RemoteDir"
chmod 600 .env
sudo docker compose -f docker-compose.prod.yml up -d --build
for i in `$(seq 1 30); do
  if sudo docker compose -f docker-compose.prod.yml exec -T api python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
sudo docker compose -f docker-compose.prod.yml exec -T api alembic upgrade head
sudo docker compose -f docker-compose.prod.yml exec -T api python -m app.infrastructure.db.seed
for i in `$(seq 1 30); do
  if sudo docker compose -f docker-compose.prod.yml exec -T n8n wget -q -O - http://localhost:5678/healthz >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
"@
Invoke-RemoteBash -ScriptText $DeployScript

if (-not $SkipWorkflowSync) {
    Write-Host "[5/6] Sinkronisasi dan publikasi ulang workflow n8n..."
    $WorkflowFiles = Get-ChildItem -Path (Join-Path $RepoRoot "n8n\workflows\*.json") | Sort-Object Name
    $WorkflowEntries = New-Object System.Collections.Generic.List[object]
    foreach ($wfFile in $WorkflowFiles) {
        $json = Get-Content -Path $wfFile.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        $items = if ($json -is [System.Array]) { $json } else { @($json) }
        foreach ($item in $items) {
            if ($item.id) {
                $WorkflowEntries.Add([pscustomobject]@{ File = $wfFile.Name; Id = $item.id })
            }
        }
    }
    $ImportLines = New-Object System.Collections.Generic.List[string]
    $PublishLines = New-Object System.Collections.Generic.List[string]
    # Publikasikan sub-workflow dan trigger jadwal lebih dulu, lalu webhook utama (01)
    $Ordered = $WorkflowEntries | Sort-Object { if ($_.File.StartsWith("01_")) { "99_" + $_.File } else { $_.File } }
    foreach ($entry in $Ordered) {
        $ImportLines.Add("sudo docker cp `"$RemoteDir/n8n/workflows/$($entry.File)`" tackle-n8n:/tmp/$($entry.File)")
        $ImportLines.Add("sudo docker compose -f docker-compose.prod.yml exec -T n8n n8n import:workflow --input=/tmp/$($entry.File)")
        $ImportLines.Add("sudo docker compose -f docker-compose.prod.yml exec -u root -T n8n rm -f /tmp/$($entry.File)")
        $PublishLines.Add("sudo docker compose -f docker-compose.prod.yml exec -T n8n n8n unpublish:workflow --id=$($entry.Id) >/dev/null 2>&1 || true")
        $PublishLines.Add("sleep 2")
        $PublishLines.Add("sudo docker compose -f docker-compose.prod.yml exec -T n8n n8n publish:workflow --id=$($entry.Id)")
    }
    $WorkflowScript = @"
cd "$RemoteDir"
$($ImportLines -join "`n")
$($PublishLines -join "`n")
sudo docker compose -f docker-compose.prod.yml exec -T n8n n8n list:workflow
"@
    Invoke-RemoteBash -ScriptText $WorkflowScript
}
else {
    Write-Host "[5/6] Sinkronisasi workflow dilewati (-SkipWorkflowSync)."
}

Write-Host "[6/6] Status layanan di VM:"
Invoke-RemoteBash -ScriptText "cd `"$RemoteDir`" && sudo docker compose -f docker-compose.prod.yml ps"
Write-Host "Deploy selesai. Webhook base: $WebhookUrl"

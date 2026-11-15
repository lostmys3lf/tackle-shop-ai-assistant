@echo off
setlocal

echo ========================================================================
echo PERINGATAN: Pastikan VM GCP sudah dimatikan sebelum menjalankan mode lokal.
echo Webhook Telegram hanya bisa mengarah ke satu tempat (VM GCP atau laptop ini).
echo ========================================================================

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop'; " ^
  "$root = (Get-Location).Path; " ^
  "$envFile = Join-Path $root '.env'; " ^
  "if (-not (Test-Path $envFile)) { Write-Error 'File .env tidak ditemukan di root project.'; exit 1 }; " ^
  "if (Get-Command gcloud -ErrorAction SilentlyContinue) { " ^
  "  $vmStatus = (& gcloud compute instances describe tackle-shop --project=neat-geode-514006-g4 --zone=asia-southeast2-a --format='get(status)' 2>$null); " ^
  "  if ($LASTEXITCODE -eq 0 -and $vmStatus -eq 'RUNNING') { Write-Warning 'VM tackle-shop di GCP masih RUNNING! Menjalankan lokal akan merebut webhook Telegram dari VM.' }; " ^
  "}; " ^
  "& docker compose up -d; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; " ^
  "$tunnelUrl = $null; " ^
  "for ($i = 0; $i -lt 30; $i++) { " ^
  "  $logs = (& docker logs tackle-cloudflared 2>&1 | Out-String); " ^
  "  if ($logs -match 'https://[a-z0-9-]+\.trycloudflare\.com') { $tunnelUrl = $Matches[0]; break }; " ^
  "  Start-Sleep -Seconds 2; " ^
  "}; " ^
  "if (-not $tunnelUrl) { Write-Error 'Gagal menemukan URL Quick Tunnel di log tackle-cloudflared.'; exit 1 }; " ^
  "$webhookBase = $tunnelUrl.TrimEnd('/') + '/'; " ^
  "$lines = Get-Content -Path $envFile -Encoding UTF8; " ^
  "$found = $false; " ^
  "$updated = foreach ($line in $lines) { if ($line -match '^WEBHOOK_URL=') { $found = $true; 'WEBHOOK_URL=' + $webhookBase } else { $line } }; " ^
  "if (-not $found) { $updated += ('WEBHOOK_URL=' + $webhookBase) }; " ^
  "[System.IO.File]::WriteAllLines($envFile, $updated, [System.Text.UTF8Encoding]::new($false)); " ^
  "Write-Host ('WEBHOOK_URL diperbarui ke: ' + $webhookBase); " ^
  "& docker compose up -d --force-recreate n8n; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; " ^
  "for ($j = 0; $j -lt 30; $j++) { " ^
  "  & docker compose exec -T n8n wget -q -O - http://localhost:5678/healthz >$null 2>&1; " ^
  "  if ($LASTEXITCODE -eq 0) { break }; " ^
  "  Start-Sleep -Seconds 2; " ^
  "}; " ^
  "Start-Sleep -Seconds 3; " ^
  "& docker compose exec -T n8n n8n unpublish:workflow --id=8BTnG1dgWGLOK0ZV >$null 2>&1; " ^
  "Start-Sleep -Seconds 2; " ^
  "& docker compose exec -T n8n n8n publish:workflow --id=8BTnG1dgWGLOK0ZV; " ^
  "& docker compose ps; " ^
  "Write-Host ''; Write-Host ('Layanan lokal siap. Tunnel webhook: ' + $webhookBase)"

endlocal

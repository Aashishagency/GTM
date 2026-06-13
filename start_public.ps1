# start_public.ps1 — run the GTM app on a public URL (Flask + cloudflared tunnel).
# Usage:  right-click → Run with PowerShell, or:  powershell -ExecutionPolicy Bypass -File start_public.ps1
# It starts a Cloudflare quick tunnel, writes the public URL into gtm_workflow\.env
# (APP_BASE_URL), then launches the app. Stop everything with Ctrl+C.

$ErrorActionPreference = "Stop"
$root   = "C:\Users\User\Desktop\claude"
$py     = "C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe"
$cf     = Join-Path $root "tools\cloudflared.exe"
$envf   = Join-Path $root "gtm_workflow\.env"
$cflog  = Join-Path $root "tools\cf.log"

if (Test-Path $cflog) { Remove-Item $cflog -Force }
Write-Host "Starting Cloudflare tunnel..." -ForegroundColor Cyan
$tunnel = Start-Process -FilePath $cf `
  -ArgumentList @("tunnel","--url","http://localhost:5000","--logfile",$cflog,"--loglevel","info") `
  -PassThru -WindowStyle Hidden

# Wait for the public URL to appear in the log
$url = $null
for ($i=0; $i -lt 20; $i++) {
  Start-Sleep -Seconds 2
  if (Test-Path $cflog) {
    $m = Select-String -Path $cflog -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -First 1
    if ($m) { $url = ($m.Matches[0].Value); break }
  }
}
if (-not $url) { Write-Host "Could not get tunnel URL — check $cflog" -ForegroundColor Red; exit 1 }
Write-Host "Public URL: $url" -ForegroundColor Green

# Write APP_BASE_URL into .env (replace existing line)
$lines = Get-Content $envf
$found = $false
$lines = $lines | ForEach-Object {
  if ($_ -match '^APP_BASE_URL=') { $found = $true; "APP_BASE_URL=$url" } else { $_ }
}
if (-not $found) { $lines += "APP_BASE_URL=$url" }
Set-Content -Path $envf -Value $lines -Encoding utf8
Write-Host "Wrote APP_BASE_URL into .env" -ForegroundColor Green

Write-Host "`nLaunching the app...  (open $url in your browser)" -ForegroundColor Cyan
Write-Host "Login: see APP_USERNAME / APP_PASSWORD in gtm_workflow\.env`n" -ForegroundColor Yellow
$env:FLASK_DEBUG = "0"        # no reloader, so the scheduler starts once
$env:RUN_SCHEDULER = "1"
try {
  & $py (Join-Path $root "gtm_workflow\app.py")
} finally {
  if ($tunnel -and -not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force }
  Write-Host "Tunnel stopped." -ForegroundColor Cyan
}

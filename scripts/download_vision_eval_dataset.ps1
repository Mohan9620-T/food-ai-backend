$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$manifestPath = Join-Path $repoRoot "docs/vision-eval-dataset/labels.json"
$datasetRoot = Split-Path -Parent $manifestPath
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$headers = @{ "User-Agent" = "food-ai-backend-evaluation-dataset/1.0" }
$downloadStarted = $false

foreach ($entry in $manifest.images) {
    $destination = Join-Path $datasetRoot $entry.file
    $directory = Split-Path -Parent $destination
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    if (Test-Path -LiteralPath $destination) {
        Write-Output "EXISTS $($entry.id) $destination"
        continue
    }
    if (-not $entry.download_url) {
        Write-Output "DERIVED $($entry.id) run scripts/build_vision_eval_variants.py"
        continue
    }
    Start-Sleep -Seconds $(if ($downloadStarted) { 5 } else { 30 })
    Invoke-WebRequest -Uri $entry.download_url -Headers $headers -OutFile $destination
    $downloadStarted = $true
    Write-Output "DOWNLOADED $($entry.id) $destination"
}

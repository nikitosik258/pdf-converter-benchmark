$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

Write-Host "Fast tests in main environment..."
& (Join-Path $Root ".venv\Scripts\python.exe") -m pytest -q -m "not heavy"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$env:RUN_HEAVY_LOCAL_TESTS = "1"
$env:PYTHONUNBUFFERED = "1"

Write-Host ""
Write-Host "Docling smoke test in main environment..."
$DoclingTarget = (Join-Path $Root "tests\test_heavy_local_adapters.py") + "::test_docling_minimal_pdf"
& (Join-Path $Root ".venv\Scripts\python.exe") -m pytest $DoclingTarget -vv -s --maxfail=1
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Marker smoke test in isolated environment..."
& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "test_marker.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "MinerU smoke test in isolated environment..."
$MinerUTarget = (Join-Path $Root "tests\test_heavy_local_adapters.py") + "::test_mineru_minimal_pdf"
& (Join-Path $Root ".venv-mineru\Scripts\python.exe") -m pytest $MinerUTarget -vv -s --maxfail=1
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "All local adapter smoke tests completed."

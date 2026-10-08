$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

Write-Host "1/2 Compile patched Mindee adapter and live checker..."
& (Join-Path $Root ".venv\Scripts\python.exe") -m py_compile `
    (Join-Path $Root "src\pdf_benchmark\adapters\cloud\mindee_adapter.py") `
    (Join-Path $Root "scripts\check_mindee_live.py")

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "2/2 Re-run Prompt-8 cloud mock regression tests..."
& (Join-Path $Root ".venv-cloud\Scripts\python.exe") -m pytest -q `
    (Join-Path $Root "tests\test_cloud_mock_adapters.py")

exit $LASTEXITCODE

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$CloudPython = Join-Path $Root ".venv-cloud\Scripts\python.exe"

Write-Host "[1/5] Verifying preserved Azure implementation..."
$AzureRequired = @(
    "src\pdf_benchmark\adapters\cloud\azure_document_intelligence_adapter.py",
    "config\tools\azure_document_intelligence.yaml",
    "tests\fixtures\cloud\azure.json"
)
foreach ($Rel in $AzureRequired) {
    $P = Join-Path $Root $Rel
    if (-not (Test-Path $P)) { throw "Azure preservation check failed; missing: $Rel" }
    Write-Host "Retained: $Rel"
}

Write-Host "[2/5] Installing pinned LlamaParse SDK into existing .venv-cloud..."
if (-not (Test-Path $CloudPython)) { throw ".venv-cloud does not exist: $CloudPython" }
uv pip install --python $CloudPython "llama-cloud==2.16.0"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[3/5] Compiling patched Python files..."
& (Join-Path $Root ".venv\Scripts\python.exe") -m py_compile `
    (Join-Path $Root "src\pdf_benchmark\adapters\cloud\llamaparse_adapter.py") `
    (Join-Path $Root "src\pdf_benchmark\benchmark\experiment.py") `
    (Join-Path $Root "src\pdf_benchmark\benchmark\registry.py") `
    (Join-Path $Root "src\pdf_benchmark\benchmark\worker.py") `
    (Join-Path $Root "scripts\check_llamaparse_live.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[4/5] Running cloud mock regressions (LlamaParse + preserved Azure included)..."
& $CloudPython -m pytest -q (Join-Path $Root "tests\test_cloud_mock_adapters.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[5/5] Verifying active-provider switch state and Azure retention..."
& (Join-Path $Root ".venv\Scripts\python.exe") -m pytest -q (Join-Path $Root "tests\test_llamaparse_switch.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "LlamaParse is ACTIVE. Azure code/config/fixture are PRESERVED but INACTIVE."
Write-Host "To switch back later:"
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts/switch_fifth_cloud.ps1 -Provider azure"

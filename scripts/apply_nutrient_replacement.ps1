$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

Write-Host "[1/4] Removing obsolete Google Cloud Vision benchmark files..."
$obsolete = @(
  "src\pdf_benchmark\adapters\cloud\google_cloud_vision_adapter.py",
  "config\tools\google_cloud_vision.yaml",
  "tests\fixtures\cloud\google_cloud_vision.json"
)
foreach ($rel in $obsolete) {
  $p = Join-Path $Root $rel
  if (Test-Path $p) { Remove-Item $p -Force; Write-Host "Removed $rel" }
}

Write-Host "[2/4] Installing Nutrient client into existing .venv-cloud..."
uv pip install --python (Join-Path $Root ".venv-cloud\Scripts\python.exe") "nutrient-dws==3.1.0"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[3/4] Compiling patched files..."
& (Join-Path $Root ".venv\Scripts\python.exe") -m py_compile `
  (Join-Path $Root "src\pdf_benchmark\adapters\cloud\nutrient_adapter.py") `
  (Join-Path $Root "scripts\probe_cloud_credentials.py") `
  (Join-Path $Root "scripts\probe_tool_environment.py") `
  (Join-Path $Root "scripts\check_nutrient_live.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[4/4] Running cloud mock regressions..."
& (Join-Path $Root ".venv-cloud\Scripts\python.exe") -m pytest -q tests/test_cloud_mock_adapters.py
exit $LASTEXITCODE

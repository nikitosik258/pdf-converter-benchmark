$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

# Remove only obsolete Prompt-8 files generated earlier for the wrong service selection.
$Obsolete = @(
    "src\pdf_benchmark\adapters\cloud\mathpix_adapter.py",
    "src\pdf_benchmark\adapters\cloud\google_document_ai_adapter.py",
    "src\pdf_benchmark\adapters\cloud\amazon_textract_adapter.py",
    "config\tools\mathpix.yaml",
    "config\tools\google_document_ai.yaml",
    "config\tools\amazon_textract.yaml",
    "tests\fixtures\cloud\mathpix.json",
    "tests\fixtures\cloud\google_document_ai.json",
    "tests\fixtures\cloud\amazon_textract.json",
    "src\pdf_benchmark\adapters\cloud\google_cloud_vision_adapter.py",
    "config\tools\google_cloud_vision.yaml",
    "tests\fixtures\cloud\google_cloud_vision.json"
)
foreach ($Rel in $Obsolete) {
    $P = Join-Path $Root $Rel
    if (Test-Path $P) {
        Remove-Item $P -Force
        Write-Host "Removed obsolete file: $Rel"
    }
}

Write-Host "[1/3] Ensuring Python 3.12..."
uv python install 3.12

Write-Host "[2/3] Creating isolated cloud environment (.venv-cloud)..."
uv venv (Join-Path $Root ".venv-cloud") --python 3.12 --clear

Write-Host "[3/3] Installing selected cloud adapters and tests..."
uv pip install --python (Join-Path $Root ".venv-cloud\Scripts\python.exe") -e "$Root[cloud-all,test]"

Write-Host "Cloud environment ready. Running credential-free mock tests..."
& (Join-Path $Root ".venv-cloud\Scripts\python.exe") -m pytest -q tests/test_cloud_mock_adapters.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Cloud mock tests passed."

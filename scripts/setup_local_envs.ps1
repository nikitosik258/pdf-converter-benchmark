$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

Write-Host "[1/7] Ensuring Python 3.12 is available via uv..."
uv python install 3.12

Write-Host "[2/7] Creating main environment (.venv)..."
uv venv (Join-Path $Root ".venv") --python 3.12 --clear

Write-Host "[3/7] Installing PyMuPDF + pdfplumber + Docling + tests into .venv..."
uv pip install --python (Join-Path $Root ".venv\Scripts\python.exe") -e "$Root[local-core,test]"

Write-Host "[4/7] Creating Marker environment (.venv-marker)..."
uv venv (Join-Path $Root ".venv-marker") --python 3.12 --clear
uv pip install --python (Join-Path $Root ".venv-marker\Scripts\python.exe") -e "$Root[marker,test]"

Write-Host "[5/7] Installing Marker llama.cpp runtime..."
& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "setup_marker_llama.ps1")

Write-Host "[6/7] Creating MinerU environment (.venv-mineru)..."
uv venv (Join-Path $Root ".venv-mineru") --python 3.12 --clear
uv pip install --python (Join-Path $Root ".venv-mineru\Scripts\python.exe") -e "$Root[mineru,test]"

Write-Host "[7/7] Verifying imports..."
& (Join-Path $Root ".venv\Scripts\python.exe") -c "import fitz, pdfplumber, docling, pdf_benchmark; print('core OK')"
& (Join-Path $Root ".venv-marker\Scripts\python.exe") -c "import marker, pdf_benchmark; print('marker OK')"
& (Join-Path $Root ".venv-mineru\Scripts\python.exe") -c "import mineru, pdf_benchmark; print('mineru OK')"

Write-Host "All local benchmark environments are installed."

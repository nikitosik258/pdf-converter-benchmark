$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PathFile = Join-Path $Root ".tools\llama.cpp\llama-server.path"

if (-not (Test-Path $PathFile)) {
    Write-Host "llama-server is not installed. Installing it first..."
    & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "setup_marker_llama.ps1")
}

$LlamaServer = (Get-Content $PathFile -Raw).Trim()
if (-not (Test-Path $LlamaServer)) {
    throw "Invalid LLAMA_CPP_BINARY path: $LlamaServer"
}

$env:LLAMA_CPP_BINARY = $LlamaServer
$env:SURYA_INFERENCE_BACKEND = "llamacpp"
$env:RUN_HEAVY_LOCAL_TESTS = "1"
$env:PYTHONUNBUFFERED = "1"

$Python = Join-Path $Root ".venv-marker\Scripts\python.exe"
$TestTarget = (Join-Path $Root "tests\test_heavy_local_adapters.py") + "::test_marker_minimal_pdf"

Write-Host "Using llama-server:"
Write-Host "  $env:LLAMA_CPP_BINARY"
Write-Host ""
Write-Host "Running Marker smoke test..."

& $Python -m pytest $TestTarget -vv -s --maxfail=1
exit $LASTEXITCODE

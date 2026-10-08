$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
& (Join-Path $Root ".venv-cloud\Scripts\python.exe") -m pytest -q tests/test_cloud_mock_adapters.py
exit $LASTEXITCODE

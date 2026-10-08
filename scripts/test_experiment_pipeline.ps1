$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

& (Join-Path $Root ".venv\Scripts\python.exe") -m pytest -q `
    tests/test_experiment_quality_control.py

exit $LASTEXITCODE

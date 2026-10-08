$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

& (Join-Path $Root ".venv\Scripts\python.exe") -m pytest -q `
    tests/test_benchmark_config.py `
    tests/test_benchmark_ground_truth.py `
    tests/test_benchmark_matching.py `
    tests/test_benchmark_resume.py `
    tests/test_benchmark_missing_object_zero.py

exit $LASTEXITCODE

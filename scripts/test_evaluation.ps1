$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

& (Join-Path $Root ".venv\Scripts\python.exe") -m pytest -q `
    tests/test_evaluation_common.py `
    tests/test_evaluation_text.py `
    tests/test_evaluation_table.py `
    tests/test_evaluation_math.py `
    tests/test_evaluation_chemistry.py `
    tests/test_evaluation_visual.py `
    tests/test_evaluation_framework.py `
    tests/test_evaluation_aggregation.py

exit $LASTEXITCODE

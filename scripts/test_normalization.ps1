$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

# Normalization has no tool-specific dependencies, so run in the main benchmark env.
& (Join-Path $Root ".venv\Scripts\python.exe") -m pytest -q `
    tests/test_normalization_unicode.py `
    tests/test_normalization_text.py `
    tests/test_normalization_latex.py `
    tests/test_normalization_chemistry.py `
    tests/test_normalization_tables.py `
    tests/test_normalization_captions.py `
    tests/test_normalization_pipeline.py

exit $LASTEXITCODE

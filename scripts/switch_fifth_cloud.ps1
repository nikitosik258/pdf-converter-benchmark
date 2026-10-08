param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("llamaparse", "azure")]
    [string]$Provider
)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Config = Join-Path $Root "config\benchmark.yaml"
$Text = Get-Content $Config -Raw -Encoding UTF8

if ($Provider -eq "llamaparse") {
    $Target = "llamaparse"
    $Other = "azure_document_intelligence"
} else {
    $Target = "azure_document_intelligence"
    $Other = "llamaparse"
}

$HasTarget = $Text -match "(?m)^\s*-\s+$Target\s*$"
$HasOther = $Text -match "(?m)^\s*-\s+$Other\s*$"

if ($HasTarget -and -not $HasOther) {
    Write-Host "Already active: $Target"
    exit 0
}
if ($HasTarget -and $HasOther) {
    throw "Both fifth-provider entries are active. Fix config/benchmark.yaml before switching."
}
if (-not $HasOther) {
    throw "Neither switchable provider entry was found in config/benchmark.yaml."
}

$Pattern = "(?m)^(\s*-\s+)$Other(\s*)$"
$Text = [regex]::Replace($Text, $Pattern, ('${1}' + $Target + '${2}'), 1)
Set-Content -Path $Config -Value $Text -Encoding UTF8
Write-Host "Active fifth cloud provider: $Target"
Write-Host "The other provider remains installed and registered; only benchmark.yaml changed."

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ToolsDir = Join-Path $Root ".tools\llama.cpp"
$PathFile = Join-Path $ToolsDir "llama-server.path"

# Reuse a previously installed binary.
if (Test-Path $PathFile) {
    $Existing = (Get-Content $PathFile -Raw).Trim()
    if ($Existing -and (Test-Path $Existing)) {
        Write-Host "llama-server already installed:"
        Write-Host "  $Existing"
        exit 0
    }
}

New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null

$Headers = @{
    "User-Agent" = "pdf-benchmark-marker-setup"
    "Accept"      = "application/vnd.github+json"
}

Write-Host "Searching recent llama.cpp releases for a Windows x64 CPU binary..."

# Important:
# /releases/latest currently points to the semantic stable release (for example
# v0.4.1), whose release page may link to a nightly build rather than carrying
# the Windows binary as a direct asset. Therefore scan recent releases,
# including prereleases, and pick the newest release that actually contains
# the required asset.
$Releases = Invoke-RestMethod `
    -Headers $Headers `
    -Uri "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=50"

$SelectedRelease = $null
$Asset = $null

foreach ($Release in $Releases) {
    if ($Release.draft) {
        continue
    }

    $Candidate = $Release.assets |
        Where-Object {
            $_.name -match '^llama-.+-bin-win-cpu-x64\.zip$' -or
            $_.name -eq 'llama-bin-win-cpu-x64.zip'
        } |
        Select-Object -First 1

    if ($Candidate) {
        $SelectedRelease = $Release
        $Asset = $Candidate
        break
    }
}

if (-not $Asset) {
    # Diagnostic output makes future naming changes easy to debug.
    $Names = @()
    foreach ($Release in ($Releases | Select-Object -First 10)) {
        foreach ($A in $Release.assets) {
            if ($A.name -match 'win.*x64.*\.zip$') {
                $Names += "$($Release.tag_name): $($A.name)"
            }
        }
    }

    $Diagnostic = if ($Names.Count -gt 0) {
        "`nRecent Windows x64 assets seen:`n  " + ($Names -join "`n  ")
    } else {
        "`nNo Windows x64 ZIP assets were returned by the GitHub Releases API."
    }

    throw "Could not find a llama.cpp Windows x64 CPU release asset.$Diagnostic"
}

Write-Host "Selected release: $($SelectedRelease.tag_name)"
Write-Host "Selected asset:   $($Asset.name)"

$VersionDir = Join-Path $ToolsDir $SelectedRelease.tag_name
$ZipPath = Join-Path $ToolsDir $Asset.name

if (-not (Test-Path $VersionDir)) {
    Write-Host "Downloading $($Asset.name) ..."
    Invoke-WebRequest `
        -Headers $Headers `
        -Uri $Asset.browser_download_url `
        -OutFile $ZipPath

    Write-Host "Extracting llama.cpp ..."
    New-Item -ItemType Directory -Force -Path $VersionDir | Out-Null
    Expand-Archive -Path $ZipPath -DestinationPath $VersionDir -Force
    Remove-Item $ZipPath -Force
}

$Server = Get-ChildItem `
    -Path $VersionDir `
    -Filter "llama-server.exe" `
    -Recurse `
    -File |
    Select-Object -First 1

if (-not $Server) {
    throw "llama-server.exe was not found after extracting $VersionDir"
}

$Server.FullName | Set-Content -Path $PathFile -Encoding utf8

Write-Host ""
Write-Host "Marker local inference runtime installed:"
Write-Host "  $($Server.FullName)"
Write-Host ""
Write-Host "The benchmark will use this binary automatically."

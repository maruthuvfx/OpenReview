<#
.SYNOPSIS
    OpenReview v1.0.0 GitHub Publishing and Release Automation Helper.

.DESCRIPTION
    Verifies git status, ensures the repository remote is configured for maruthuvfx/OpenReview,
    creates the signed/annotated git release tag 'v1.0.0', pushes branch 'main' and tags,
    and provides release upload assistance for all binary distribution packages.
#>

[CmdletBinding()]
param(
    [string]$Version = "1.3.0",
    [string]$TagName = "v1.3.0",
    [string]$RemoteName = "origin",
    [string]$RepoUrl = "https://github.com/maruthuvfx/OpenReview.git",
    [switch]$SkipPush
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " OpenReview v$Version GitHub Release & Distribution Helper" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Check Git
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Error "Git executable not found in PATH."
}

# 2. Check Repository Status
if (-not (Test-Path ".git")) {
    Write-Host "[1/5] Initializing local git repository..." -ForegroundColor Yellow
    git init -b main
} else {
    Write-Host "[1/5] Local git repository detected." -ForegroundColor Green
}

# 3. Verify Remote
$currentRemote = git remote get-url $RemoteName 2>$null
if (-not $currentRemote) {
    Write-Host "[2/5] Adding remote '$RemoteName' -> $RepoUrl" -ForegroundColor Yellow
    git remote add $RemoteName $RepoUrl
} else {
    Write-Host "[2/5] Git remote '$RemoteName' configured: $currentRemote" -ForegroundColor Green
}

# 4. Check Distribution Binaries
$DistDir = Join-Path $ProjectRoot "Openreview_build"
$ExpectedAssets = @(
    "OpenReview-$Version-win64.msi",
    "OpenReview-$Version-win64-portable.zip",
    "openreview_${Version}_amd64.deb",
    "openreview-$Version-1.x86_64.rpm"
)

Write-Host "`n[3/5] Verifying distribution packages in Openreview_build/:" -ForegroundColor Cyan
$missingAssets = @()
foreach ($asset in $ExpectedAssets) {
    $assetPath = Join-Path $DistDir $asset
    if (Test-Path $assetPath) {
        $sizeMb = [math]::Round((Get-Item $assetPath).Length / 1MB, 2)
        Write-Host "  [OK] $asset ($sizeMb MB)" -ForegroundColor Green
    } else {
        Write-Host "  [MISSING] $asset" -ForegroundColor Red
        $missingAssets += $asset
    }
}

if ($missingAssets.Count -gt 0) {
    Write-Warning "Some distribution packages are missing. Run packaging scripts before publishing."
}

# 5. Tagging
Write-Host "`n[4/5] Checking release tag '$TagName'..." -ForegroundColor Cyan
$tagExists = git tag -l $TagName
if (-not $tagExists) {
    Write-Host "Creating tag '$TagName'..." -ForegroundColor Yellow
    git tag -a $TagName -m "OpenReview v1.0.0 Official Production Release"
    Write-Host "Tag '$TagName' created successfully." -ForegroundColor Green
} else {
    Write-Host "Tag '$TagName' already exists." -ForegroundColor Green
}

# 6. Push & Release Instructions
Write-Host "`n[5/5] Ready to publish to GitHub!" -ForegroundColor Cyan
if ($SkipPush) {
    Write-Host "SkipPush flag enabled; skipping git push." -ForegroundColor Yellow
} else {
    Write-Host "Pushing main branch and tags to $RemoteName..." -ForegroundColor Yellow
    git push $RemoteName main --tags
}

Write-Host @"

--------------------------------------------------------------------------------
 GitHub Release Asset Upload Instructions:
--------------------------------------------------------------------------------
1. Navigate to: https://github.com/maruthuvfx/OpenReview/releases/new
2. Select tag: $TagName
3. Title: OpenReview v$Version - Production Release
4. Drag and drop the following files from '$DistDir' into the release:
   - OpenReview-$Version-win64.msi
   - OpenReview-$Version-win64-portable.zip
   - openreview_${Version}_amd64.deb
   - openreview-$Version-1.x86_64.rpm
5. Click 'Publish release'.
--------------------------------------------------------------------------------
"@ -ForegroundColor Green

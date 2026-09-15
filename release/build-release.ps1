# Windows one-file build. Run from the repository root with:
# powershell -NoProfile -File .\release\build-release.ps1
# The spec keeps RapidOCR's config, three ONNX models, Windows tzdata and
# required GUI/image modules. It excludes only inspected optional plugins.
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    python -m PyInstaller --noconfirm `
        --distpath $PSScriptRoot `
        --workpath (Join-Path $PSScriptRoot 'build-slim') `
        (Join-Path $PSScriptRoot 'CourseCalendar.spec')
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

param(
    [string]$Python = "python",
    [string]$Iscc = "ISCC.exe"
)

$ErrorActionPreference = "Stop"
Push-Location (Join-Path $PSScriptRoot "..")
try {
    $architecture = & $Python -c "import platform; print(platform.machine().upper())"
    if ($LASTEXITCODE -ne 0 -or $architecture -ne "AMD64") {
        throw "Building the x64 installer requires x64 Python on Windows."
    }
    $versionText = & $Python wordbook.py --version
    if ($LASTEXITCODE -ne 0 -or $versionText -notmatch '^wordbook (\d+\.\d+\.\d+)$') {
        throw "Could not read Wordbook's version."
    }
    $version = $Matches[1]
    if ($env:GITHUB_REF_TYPE -eq "tag" -and $env:GITHUB_REF_NAME -ne "v$version") {
        throw "The release tag must be v$version."
    }
    $icon = (Get-ChildItem -File -Filter "*.ico" | Select-Object -First 1).FullName
    $html = (Resolve-Path index.html).Path
    $license = (Resolve-Path LICENSE).Path
    $pythonHome = & $Python -c "import sys; print(sys.base_prefix)"
    $pythonLicense = Join-Path $pythonHome 'LICENSE.txt'
    & $Python -m PyInstaller --noconfirm --clean --onedir --windowed --noupx `
        --name Wordbook --icon $icon --add-data "$html;." --add-data "$license;." `
        --add-data "$pythonLicense;licenses/python" `
        --distpath build/dist --workpath build/pyinstaller --specpath build windows_launcher.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

    & $Iscc "/DAppVersion=$version" "/DAppIcon=$icon" packaging/wordbook.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed." }
}
finally {
    Pop-Location
}

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot

$PythonLauncher = Get-Command py -ErrorAction SilentlyContinue
if (-not $PythonLauncher) {
    throw "Python launcher 'py' was not found. Install Python 3.14 and enable the Python launcher, then rerun this script."
}

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    & py -3.14 -m venv (Join-Path $ProjectRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Could not create the project virtual environment." }
}

$PipAvailable = & $Python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('pip') else 1)"
if ($LASTEXITCODE -eq 0) {
    & $Python -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
} else {
    $DependenciesAvailable = & $Python -c "import django, dotenv, openpyxl, mysql.connector"
    if ($LASTEXITCODE -ne 0) {
        & $Python -m ensurepip --upgrade
        if ($LASTEXITCODE -ne 0) { throw "Could not install pip into .venv." }
        & $Python -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
        if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
    } else {
        Write-Host "Dependencies are already available in .venv; pip bootstrap is not needed." -ForegroundColor DarkGray
    }
}

$EnvironmentFile = Join-Path $ProjectRoot ".env"
if (-not (Test-Path $EnvironmentFile)) {
    Copy-Item (Join-Path $ProjectRoot ".env.example") $EnvironmentFile
    $Secret = & $Python -c "import secrets; print(secrets.token_urlsafe(48))"
    if ($LASTEXITCODE -ne 0 -or -not $Secret) { throw "Could not generate a Django secret key." }
    $EnvironmentText = [System.IO.File]::ReadAllText($EnvironmentFile)
    $EnvironmentText = $EnvironmentText.Replace("replace-this-with-a-long-random-value", $Secret.Trim())
    [System.IO.File]::WriteAllText($EnvironmentFile, $EnvironmentText, [System.Text.UTF8Encoding]::new($false))
}

& $Python (Join-Path $ProjectRoot "manage.py") check
if ($LASTEXITCODE -ne 0) { throw "Django's system check failed." }
& $Python (Join-Path $ProjectRoot "manage.py") migrate --noinput
if ($LASTEXITCODE -ne 0) { throw "Database migrations failed." }

Write-Host "Roster app ready at http://127.0.0.1:8000/" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop the development server." -ForegroundColor DarkGray
& $Python (Join-Path $ProjectRoot "manage.py") runserver 127.0.0.1:8000
exit $LASTEXITCODE

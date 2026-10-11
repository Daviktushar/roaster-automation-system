param(
    [string]$DatabaseName = "roster_automation",
    [string]$ApplicationUser = "roster_app",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 3306
)

$ErrorActionPreference = "Stop"
if ($DatabaseName -notmatch '^[A-Za-z][A-Za-z0-9_]{0,63}$') { throw "DatabaseName must contain only letters, digits, and underscores." }
if ($ApplicationUser -notmatch '^[A-Za-z][A-Za-z0-9_]{0,31}$') { throw "ApplicationUser must contain only letters, digits, and underscores." }
if (Test-Path -LiteralPath ".env") { throw "A .env file already exists. Keep it safe and configure the MySQL fields manually instead of overwriting it." }

$mysqlCommand = Get-Command mysql.exe -ErrorAction Stop
$adminSecure = Read-Host "MySQL administrator password (input is hidden)" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($adminSecure)
$adminPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
$randomBytes = New-Object byte[] 36
$randomGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
$randomGenerator.GetBytes($randomBytes)
$randomGenerator.Dispose()
$applicationPassword = [Convert]::ToBase64String($randomBytes).TrimEnd("=").Replace("+", "_").Replace("/", "-")
$tempOptionFile = Join-Path ([IO.Path]::GetTempPath()) ("roster-mysql-" + [Guid]::NewGuid().ToString("N") + ".cnf")

try {
    $escapedAdminPassword = $adminPassword.Replace("\", "\\").Replace('"', '\"')
    [IO.File]::WriteAllText($tempOptionFile, "[client]`r`nuser=root`r`npassword=`"$escapedAdminPassword`"`r`n", [Text.Encoding]::ASCII)
    $escapedAppPassword = $applicationPassword.Replace("'", "''")
    $sql = "CREATE DATABASE IF NOT EXISTS ``$DatabaseName`` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci; CREATE USER '$ApplicationUser'@'$HostName' IDENTIFIED BY '$escapedAppPassword'; GRANT ALL PRIVILEGES ON ``$DatabaseName``.* TO '$ApplicationUser'@'$HostName';"
    & $mysqlCommand.Source "--defaults-extra-file=$tempOptionFile" -h $HostName -P $Port -e $sql
    if ($LASTEXITCODE -ne 0) { throw "MySQL setup failed with exit code $LASTEXITCODE. No .env file was written." }

    $secretKey = [Guid]::NewGuid().ToString("N") + [Guid]::NewGuid().ToString("N")
    $envText = @"
DJANGO_SECRET_KEY=$secretKey
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost
DB_ENGINE=mysql
DB_NAME=$DatabaseName
DB_USER=$ApplicationUser
DB_PASSWORD=$applicationPassword
DB_HOST=$HostName
DB_PORT=$Port
"@
    [IO.File]::WriteAllText((Join-Path (Get-Location) ".env"), $envText.Trim() + "`r`n", [Text.UTF8Encoding]::new($false))
    Write-Host "Created database '$DatabaseName', user '$ApplicationUser', and a local .env file. Credentials were not printed."
}
finally {
    if (Test-Path -LiteralPath $tempOptionFile) { Remove-Item -LiteralPath $tempOptionFile -Force }
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    $adminPassword = $null
    $applicationPassword = $null
    [Array]::Clear($randomBytes, 0, $randomBytes.Length)
}

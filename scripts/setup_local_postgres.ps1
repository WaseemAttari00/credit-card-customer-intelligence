# Sets up and starts a portable (no-install, no-admin) PostgreSQL cluster on Windows.
# Only needed if you don't already have a Postgres server. Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_local_postgres.ps1           # init (first time) + start
#   powershell -ExecutionPolicy Bypass -File scripts\setup_local_postgres.ps1 -Stop     # stop the server
#
# Prerequisite: download the "Windows x86-64" binaries zip from
# https://www.enterprisedb.com/download-postgresql-binaries and unzip it so that
# $PG_HOME\bin\pg_ctl.exe exists (default PG_HOME = %USERPROFILE%\pgsql).
param([switch]$Stop, [int]$Port = 5432, [string]$Database = "cc_intel")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"

$pgHome = if ($env:PG_HOME) { $env:PG_HOME } else { Join-Path $env:USERPROFILE "pgsql" }
$pgData = if ($env:PG_DATA) { $env:PG_DATA } else { Join-Path $env:USERPROFILE "pgsql_data" }
$bin = Join-Path $pgHome "bin"
if (-not (Test-Path (Join-Path $bin "pg_ctl.exe"))) { throw "pg_ctl.exe not found under $bin. Set PG_HOME." }

$logFile = Join-Path $pgData "server.log"

if ($Stop) {
    & (Join-Path $bin "pg_ctl.exe") -D $pgData stop -m fast
    return
}

if (-not (Test-Path (Join-Path $pgData "PG_VERSION"))) {
    # First run: generate a random superuser password and record it only in .env (gitignored)
    $password = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ })
    $pwFile = New-TemporaryFile
    Set-Content -Path $pwFile -Value $password -NoNewline -Encoding ascii
    & (Join-Path $bin "initdb.exe") -D $pgData -U postgres --auth=scram-sha-256 --pwfile=$pwFile --encoding=UTF8 --locale=C
    Remove-Item $pwFile
    $url = "postgresql://postgres:$password@localhost:$Port/$Database"
    $lines = @("DATABASE_URL=$url", "PG_HOME=$pgHome", "PG_DATA=$pgData")
    Set-Content -Path $envFile -Value $lines -Encoding ascii
    Write-Host "Initialized cluster at $pgData; wrote DATABASE_URL to .env"
}

$status = & (Join-Path $bin "pg_ctl.exe") -D $pgData status 2>$null
if ($LASTEXITCODE -ne 0) {
    & (Join-Path $bin "pg_ctl.exe") -D $pgData -l $logFile -o "-p $Port" -w start
}

# Create the project database if it does not exist yet
$url = (Get-Content $envFile | Where-Object { $_ -like "DATABASE_URL=*" }) -replace "^DATABASE_URL=", ""
$adminUrl = $url -replace "/[^/]+$", "/postgres"
$exists = & (Join-Path $bin "psql.exe") $adminUrl -tAc "SELECT 1 FROM pg_database WHERE datname = '$Database'"
if (-not $exists) {
    & (Join-Path $bin "psql.exe") $adminUrl -c "CREATE DATABASE $Database"
}
Write-Host "PostgreSQL is running on port $Port (database: $Database)"

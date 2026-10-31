param(
    [string]$OutputDirectory = "backups",
    [string]$DatabaseName
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$containerId = (docker compose ps -q db).Trim()
if ($LASTEXITCODE -ne 0 -or -not $containerId) { throw "Compose database is not running" }
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$tempName = "supportflow-backup-$([guid]::NewGuid().ToString('N')).dump"
$containerPath = "/tmp/$tempName"
$backupPath = Join-Path $OutputDirectory "supportflow-$stamp.dump"
if ($DatabaseName -and $DatabaseName -notmatch '^[a-zA-Z0-9_]+$') {
    throw "DatabaseName must contain only letters, digits, and underscores"
}
$database = if ($DatabaseName) { '"' + $DatabaseName + '"' } else { '"$POSTGRES_DB"' }
$dumpCommand = 'pg_dump --format=custom --no-owner --username="$POSTGRES_USER" ' + $database + ' > ' + $containerPath
docker compose exec -T db sh -lc $dumpCommand
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed inside the database container" }
try {
    docker cp "${containerId}:$containerPath" $backupPath
    if ($LASTEXITCODE -ne 0) { throw "Could not copy backup archive from database container" }
    docker cp $backupPath "${containerId}:$containerPath"
    if ($LASTEXITCODE -ne 0) { throw "Could not stage backup for archive validation" }
    docker compose exec -T db pg_restore --list $containerPath | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "pg_restore could not inspect the archive" }
} finally {
    docker compose exec -T db rm -f $containerPath | Out-Null
}
Write-Output "Backup archive created and checked: $backupPath"

param([Parameter(Mandatory = $true)][string]$BackupPath)

$ErrorActionPreference = "Stop"
$resolvedBackup = (Resolve-Path -LiteralPath $BackupPath).Path
$containerId = (docker compose ps -q db).Trim()
if ($LASTEXITCODE -ne 0 -or -not $containerId) { throw "Compose database is not running" }
$dbName = "supportflow_restore_$([guid]::NewGuid().ToString('N'))_test"
$tempName = "supportflow-restore-$([guid]::NewGuid().ToString('N')).dump"
$containerPath = "/tmp/$tempName"
$dbUser = (docker compose exec -T db printenv POSTGRES_USER).Trim()
if ($LASTEXITCODE -ne 0 -or $dbUser -notmatch '^[a-zA-Z0-9_]+$') {
    throw "Could not safely determine the local PostgreSQL account"
}
docker compose exec -T db createdb --username $dbUser $dbName
if ($LASTEXITCODE -ne 0) { throw "Could not create isolated validation database $dbName" }
try {
    docker cp $resolvedBackup "${containerId}:$containerPath"
    if ($LASTEXITCODE -ne 0) { throw "Could not stage the backup archive" }
    docker compose exec -T db pg_restore --exit-on-error --no-owner --username=$dbUser --dbname=$dbName $containerPath
    if ($LASTEXITCODE -ne 0) { throw "Restore failed; inspect isolated database $dbName" }
    $integrityQuery = "SELECT current_database(), (SELECT version_num FROM alembic_version), (SELECT count(*) FROM organizations), (SELECT count(*) FROM tickets), (SELECT count(*) FROM ticket_embeddings);"
    docker compose exec -T db psql --username $dbUser --dbname $dbName -v ON_ERROR_STOP=1 -c $integrityQuery
    if ($LASTEXITCODE -ne 0) { throw "Integrity query failed on $dbName" }
    docker compose exec -T db psql --username $dbUser --dbname $dbName -v ON_ERROR_STOP=1 -c "SELECT extname FROM pg_extension;"
    if ($LASTEXITCODE -ne 0) { throw "pgvector extension check failed on $dbName" }
} finally {
    docker compose exec -T db rm -f $containerPath | Out-Null
}
Write-Output "Restored into isolated database $dbName (name ends in _test); it was not dropped."

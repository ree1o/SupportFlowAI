# PostgreSQL backup and recovery runbook

This procedure targets the Compose PostgreSQL 16 + pgvector service. It never stops
the application or removes its persistent volume. Store backups outside Git in
encrypted, access-controlled storage; `.gitignore` excludes `backups/`.

## Create and inspect (PowerShell)

From the repository root, when `docker compose ps` reports `db` healthy:

```powershell
./scripts/backup.ps1
```

The container reads its password from its environment; the command line and log do
not contain the password. Encrypt archive files before copying them off-machine and
protect encryption keys separately.

## Restore into a new disposable database

Use only synthetic-data archives for this demonstration. Create a unique database
whose name ends in `_test`. This process refuses collisions and leaves the disposable
database for inspection; it does not drop or overwrite a database.

```powershell
./scripts/restore-validation.ps1 -BackupPath backups\supportflow-example.dump
```

Confirm the database name ends in `_test`, the migration version is the expected
head, the integrity query completes, and `vector` is installed. For an
application-level check, grant the runtime role CONNECT on this disposable database,
point a separately launched local API process at it, and verify readiness plus
tenant-scoped reads. Do not change the normal Compose API URL for this test.

## Recovery sequence

1. Preserve the active database and volume; check PostgreSQL 16 and pgvector version.
2. Restore to a newly created isolated database and run integrity and migration checks.
3. Verify tenant RLS is enabled/forced, runtime is neither superuser nor BYPASSRLS,
   and representative tenant-scoped reads succeed.
4. Only after validation, create a separate approved operational plan for changing an
   active endpoint. This portfolio procedure never overwrites an active database.
5. Record archive timestamp, migration head, validation outcome, and operator in an
   access-controlled operations log.

This demonstrates recovery mechanics; no recovery time or recovery point objective
has been measured.

## M9 restore rehearsal

On 2026-10-31, a fresh disposable `supportflow_backup_<random>_test` database was
migrated to `0009_release_hardening`, seeded with one synthetic organization, backed up
with the script above, and restored into a new `supportflow_restore_<random>_test`
database. The restored integrity query found migration head `0009_release_hardening`,
one organization, zero tickets and zero embeddings; `plpgsql` and `vector` extensions
were present. The same FastAPI readiness handler returned `ready` while connecting to
the restored database as `supportflow_runtime`. The source and restore databases were
left intact for inspection. This is a functional restore rehearsal, not a measured RTO,
RPO, or recovery of a populated production workload.

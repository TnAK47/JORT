param([switch]$Reprendre)
$ErrorActionPreference='Stop'
$root=$PSScriptRoot
$python=Join-Path $root '.venv\Scripts\python.exe'
$record=Join-Path $root 'data\logs\migration-processus.json'
if (Test-Path -LiteralPath $record) {
    $old=Get-Content -LiteralPath $record -Raw | ConvertFrom-Json
    $p=Get-Process -Id $old.pid -ErrorAction SilentlyContinue
    if ($p -and [math]::Abs(([DateTimeOffset]$p.StartTime.ToUniversalTime()).ToUnixTimeSeconds()-$old.started_epoch)-lt 3) { throw 'Migration deja en cours.' }
}
& $python -B (Join-Path $root 'scripts\postgresql_local.py') start
if ($LASTEXITCODE-ne 0) { throw 'PostgreSQL non pret.' }
$migrationArguments=@('-B',('"'+(Join-Path $root 'scripts\migration_postgresql.py')+'"'),'--label','JO-JORT-MIGRATION')
if ($Reprendre) {$migrationArguments+='--resume'}
$p=Start-Process -FilePath $python -ArgumentList $migrationArguments -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $root 'data\logs\migration-postgresql-sortie.log') -RedirectStandardError (Join-Path $root 'data\logs\migration-postgresql-erreurs.log')
@{pid=$p.Id;started_epoch=([DateTimeOffset]$p.StartTime.ToUniversalTime()).ToUnixTimeSeconds();python=$python;script=(Join-Path $root 'scripts\migration_postgresql.py');role='migration';label='JO-JORT-MIGRATION'} | ConvertTo-Json | Set-Content -LiteralPath $record -Encoding UTF8
Write-Output ('Migration lancee. PID '+$p.Id+' ; suivi : data\maintenance\migration-postgresql.json')

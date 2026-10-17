param([switch]$SansNotification)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\jort-lifecycle.ps1')
$lockStream = $null
try {
    $lockStream = Enter-JortLaunchLock
    $status = $null
    $portOpen = Test-JortPort
    if ($portOpen) {
        try { $status = Get-JortHealth }
        catch { Write-Warning 'API indisponible : contrôle du processus enregistré avant arrêt.' }
    }

    $serverRecord = Read-JortRecord $JortPaths.ServerRecord
    if ($portOpen -and -not (Test-JortListenerRecord $serverRecord)) { throw 'Le port 8765 ne correspond pas au serveur enregistré. Arrêt refusé.' }
    if ($serverRecord -and (Get-Process -Id ([int]$serverRecord.pid) -ErrorAction SilentlyContinue)) {
        if (-not (Test-JortProcessIdentity $serverRecord 'server') -and -not (Test-JortApiOwnership $status $serverRecord)) { throw "Le PID du serveur ne correspond plus à son enregistrement JORT. Aucun arrêt forcé n’a été tenté." }
        Stop-JortOwnedProcess $serverRecord 'server' 25
    }

    # Le serveur annule normalement le worker pendant son arrêt. Ce contrôle traite aussi
    # le cas où le serveur a déjà disparu après une interruption inattendue.
    $workerRecord = Read-JortRecord $JortPaths.WorkerRecord
    if ($workerRecord -and (Get-Process -Id ([int]$workerRecord.pid) -ErrorAction SilentlyContinue)) {
        Stop-JortOwnedProcess $workerRecord 'worker' 5
    }

    if (Test-JortPort) { throw "Le port 8765 répond encore après l’arrêt ; vérifiez le processus qui l’utilise." }
    $reconciliationPattern=[regex]::Escape((Join-Path $JortPaths.Root 'scripts\reconcilier_entites_postgresql.py'))
    $reconciliationProcesses=@(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction Stop |
        Where-Object { [string]$_.CommandLine -match $reconciliationPattern -and [string]$_.CommandLine -notmatch '--status|--pause|--help' })
    if ($reconciliationProcesses.Count) {
        $reconciliationDirectory=Join-Path $JortPaths.Root 'data\maintenance\reconciliation-entites'
        New-Item -ItemType Directory -Path $reconciliationDirectory -Force | Out-Null
        New-Item -ItemType File -Path (Join-Path $reconciliationDirectory 'pause.flag') -Force | Out-Null
        foreach ($reconciliationProcess in $reconciliationProcesses) {
            try { Wait-Process -Id ([int]$reconciliationProcess.ProcessId) -Timeout 45 -ErrorAction Stop }
            catch { throw "La réconciliation termine son point de reprise. PostgreSQL reste ouvert ; relancer l'arrêt après sa pause." }
        }
    }
    # L'environnement Python isolé peut créer un processus enfant. Repérer
    # donc la commande de migration elle-même, pas seulement le PID du
    # lanceur, avant d'arrêter PostgreSQL.
    $migrationScript = [regex]::Escape((Join-Path $JortPaths.Root 'scripts\migration_postgresql.py'))
    $migrationProcesses = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue |
        Where-Object { [string]$_.CommandLine -match $migrationScript })
    if ($migrationProcesses.Count) {
        New-Item -ItemType File -Path (Join-Path $JortPaths.Root 'data\maintenance\pause-migration-postgresql.flag') -Force | Out-Null
        foreach ($migrationProcess in $migrationProcesses) {
            try { Wait-Process -Id ([int]$migrationProcess.ProcessId) -Timeout 45 -ErrorAction Stop }
            catch { throw 'La migration termine son journal ; attendre son point de reprise avant de fermer PostgreSQL.' }
        }
    }
    if (Test-Path -LiteralPath (Join-Path $JortPaths.Root 'data\postgresql\PG_VERSION')) {
        Assert-JortLocalPath (Join-Path $JortPaths.Root 'data\postgresql')
        $python = Resolve-JortPython
        & $python (Join-Path $JortPaths.Root 'scripts\postgresql_local.py') stop
        if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL ne confirme pas son arret.' }
    }
    Clear-JortRuntimeArtifacts
    try {
        $python = Resolve-JortPython
        & $python (Join-Path $JortPaths.Root 'scripts\archiver_journaux.py') --keep 8 | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Warning 'Les journaux restent conservés ; leur compression sera à reprendre.' }
    } catch { Write-Warning "Compression des journaux reportée : $($_.Exception.Message)" }
    $message = 'JORT est arrêté. Le site local et ses traitements sont fermés.'
    Write-Output $message
    if (-not $SansNotification) { (New-Object -ComObject WScript.Shell).Popup($message,3,'JORT privé',64) | Out-Null }
} catch {
    $message = "Impossible d’arrêter JORT : $($_.Exception.Message)"
    if ($SansNotification) { throw $message }
    try { (New-Object -ComObject WScript.Shell).Popup($message,0,'JORT privé',16) | Out-Null } catch { Write-Error $message }
    exit 1
} finally {
    if ($lockStream) { $lockStream.Dispose() }
}

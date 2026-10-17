[CmdletBinding()]
param(
    [ValidateRange(1956, 9999)][int]$Debut = (Get-Date).Year,
    [ValidateRange(1956, 9999)][int]$Fin = 1956,
    [ValidateRange(15, 30)][int]$IntervalleSecondes = 20,
    [switch]$NePasAttendre,
    [switch]$DemarrerSiArrete
)

# Orchestrateur discret : le serveur JORT reste l'unique téléchargeur et
# conserve les tâches d'analyse dans sa file durable.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($PSScriptRoot)
. (Join-Path $root 'scripts\jort-lifecycle.ps1')
$script:sessionContext = Get-JortSessionContext
$script:python = Resolve-JortPython
$stateDir = Join-Path $root 'data\collecte-integrale'
$statePath = Join-Path $stateDir 'etat.json'
$reportJsonPath = Join-Path $stateDir 'rapport.json'
$reportMdPath = Join-Path $stateDir 'rapport.md'
$logsDir = Join-Path $stateDir 'logs'
$api = 'http://127.0.0.1:8765'
$header = @{ 'X-JORT-Local' = '1'; 'Origin' = $api }
$script:state = $null
$script:apiFailureCount = 0
$script:lastFullReportUtc = [DateTime]::MinValue
$script:lastHealth = $null
$mutex = $null
$ownsMutex = $false
$fileLock = $null
$years = @()
if ($Debut -lt $Fin) { throw 'Debut doit être supérieur ou égal à Fin (ordre décroissant).' }
if ($Debut -gt (Get-Date).Year) { throw 'La collecte ne peut demander une année future.' }
for ($y = $Debut; $y -ge $Fin; $y--) { $years += $y }
if ($years.Count -gt ((Get-Date).Year - 1956 + 1)) { throw 'Période de collecte invalide.' }
$planText = "$Debut-$Fin|download|0"
$sha = [Security.Cryptography.SHA256]::Create()
try { $scopeId = -join ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($planText)) | ForEach-Object { $_.ToString('x2') }) }
finally { $sha.Dispose() }

$script:logPath = Join-Path $logsDir ((Get-Date).ToString('yyyyMMdd-HHmmss') + '-' + $scopeId.Substring(0,8) + '.log')

function Write-Log([string]$Message) {
    $line = '{0} {1}' -f ([DateTimeOffset]::Now.ToString('o')), $Message
    Add-Content -LiteralPath $script:logPath -Value $line -Encoding UTF8
}

function Get-SafeValue($Object, [string]$Name, $Default = $null) {
    if ($null -eq $Object) { return $Default }
    if ($Object -is [System.Collections.IDictionary]) {
        if ($Object.Contains($Name)) { return $Object[$Name] }
        return $Default
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($property) { return $property.Value }
    return $Default
}

function ConvertTo-StateHashtable($Value) {
    $result = @{}
    if ($Value -is [System.Collections.IDictionary]) {
        foreach ($key in $Value.Keys) { $result[[string]$key] = $Value[$key] }
    } elseif ($Value) {
        foreach ($property in $Value.PSObject.Properties) { $result[$property.Name] = $property.Value }
    }
    $defaults = @{
        schema_version=1; scope_id=$scopeId; run_id=''; plan_id=''; years=@($years); phase='waiting'
        submit_attempted=$false; lane_id=$null; started_at=[DateTimeOffset]::Now.ToString('o')
        updated_at=$null; message=''; publication_repairable=$false; stopped_backlog=$false
        resume_from_checkpoint=$false; resume_from_year=$null; progress=@{}
    }
    foreach ($key in $defaults.Keys) {
        if (-not $result.ContainsKey($key)) { $result[$key] = $defaults[$key] }
    }
    return $result
}

function Get-PlanOutcome($Health) {
    $lanes = Get-SafeValue $Health 'lanes'
    $collection = Get-SafeValue $lanes 'collection'
    $indexation = Get-SafeValue $lanes 'indexation'
    if ((Get-SafeValue $collection 'state') -eq 'failed') { return 'failed' }
    $errors = Get-SafeValue $collection 'errors_by_year' @{}
    $errorCount = if ($errors -is [System.Collections.IDictionary]) { $errors.Count } else { @($errors.PSObject.Properties).Count }
    $downloadErrors = Get-SafeValue $collection 'failed_downloads_by_year' @{}
    $downloadErrorCount = if ($downloadErrors -is [System.Collections.IDictionary]) { $downloadErrors.Count } else { @($downloadErrors.PSObject.Properties).Count }
    if ($errorCount -gt 0 -or @(Get-SafeValue $collection 'incomplete_years' @()).Count -gt 0 -or
        $downloadErrorCount -gt 0 -or
        [int](Get-SafeValue $indexation 'failed_journals' 0) -gt 0) { return 'complete_with_errors' }
    if ((Get-SafeValue $indexation 'state') -eq 'failed') { return 'failed' }
    return 'complete'
}

function Save-JsonAtomic([string]$Path, $Value) {
    $json = ConvertTo-Json -InputObject $Value -Depth 80
    $lastError = $null
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        $temp = Join-Path (Split-Path -Parent $Path) ('.' + [IO.Path]::GetFileName($Path) + '-' + [guid]::NewGuid().ToString('N') + '.tmp')
        try {
            [IO.File]::WriteAllText($temp, $json + [Environment]::NewLine, (New-Object Text.UTF8Encoding($false)))
            Move-Item -LiteralPath $temp -Destination $Path -Force
            return
        } catch {
            $lastError = $_
            if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue }
            if ($attempt -lt 5) { Start-Sleep -Milliseconds (100 * $attempt) }
        }
    }
    throw "Écriture atomique impossible après 5 essais pour '$Path' : $($lastError.Exception.Message)"
}

function Read-Json([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    try { return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return $null }
}

function Save-State {
    param([string]$Phase, [string]$Message)
    $script:state['phase'] = $Phase
    $script:state['message'] = $Message
    $script:state['updated_at'] = [DateTimeOffset]::Now.ToString('o')
    Save-Progress $script:lastHealth $Message
}

function Get-Health {
    try {
        $h = Invoke-RestMethod -Uri ($api + '/api/health') -Headers $header -TimeoutSec 5
    } catch {
        $script:apiFailureCount++
        if ($script:apiFailureCount -ge 3) { throw "API indisponible après trois contrôles : $($_.Exception.Message)" }
        return $null
    }
    if ((Get-SafeValue $h 'service') -ne 'jort-local') { throw 'Le port 8765 ne répond pas comme le serveur JORT.' }
    $record = Read-JortRecord $script:JortPaths.ServerRecord
    if (-not (Test-JortApiOwnership $h $record)) { throw 'Le port 8765 ne correspond pas au serveur enregistré pour ce projet.' }
    $script:apiFailureCount = 0
    $script:lastHealth = $h
    return $h
}

function Ensure-InitialServer {
    if (-not (Test-JortPort)) {
        if (-not $DemarrerSiArrete) {
            Save-State 'stopped' 'Port API fermé ; sans DemarrerSiArrete le watcher ne lance pas le serveur.'
            return $false
        }
        if (-not (Test-WorkerProcessAbsent)) {
            Save-State 'waiting' 'Port API fermé mais le processus worker existe encore ; démarrage refusé jusqua sa fin.'
            return $false
        }
        Write-Log 'Port fermé, worker absent et DemarrerSiArrete présent : lancement discret du serveur existant.'
        & (Join-Path $root 'lancer-jort.ps1') -SansNavigateur
    }
    for ($attempt=1; $attempt -le 90; $attempt++) {
        try {
            $health = Get-Health
            if ($health) { return $true }
        } catch {
            if ($script:apiFailureCount -ge 3 -and (Test-JortPort)) { Write-Log "API indisponible pendant l’attente du serveur: $($_.Exception.Message)" }
        }
        Start-Sleep -Seconds 1
    }
    Save-State 'stopped' 'Le serveur ne répond pas après les contrôles ; watcher arrêté sans autre relance.'
    return $false
}

function Test-WorkerProcessAbsent {
    $workerPath = Join-Path $root 'data\logs\travailleur-processus.json'
    $record = Read-Json $workerPath
    $pidValue = Get-SafeValue $record 'pid'
    if (-not $record -or -not $pidValue) { return $true }
    $process = Get-Process -Id ([int]$pidValue) -ErrorAction SilentlyContinue
    if (-not $process) { return $true }
    try {
        $started = Get-SafeValue $record 'started_epoch'
        if (-not $started) { return $false }
        $epoch = ([DateTimeOffset]$process.StartTime.ToUniversalTime()).ToUnixTimeSeconds()
        return ([Math]::Abs($epoch - [double]$started) -gt 10)
    } catch { return $false }
}

function Get-IdleSnapshot([switch]$AllowStoppedBacklog) {
    $h = Get-Health
    if (-not $h) { return $null }
    $queue = Get-SafeValue $h 'queue' @{}
    $pending = [int](Get-SafeValue $queue 'pending' 0)
    $active = [int](Get-SafeValue $queue 'active' 0)
    $lanes = Get-SafeValue $h 'lanes' ([pscustomobject]@{})
    $laneBusy = @($lanes.PSObject.Properties | Where-Object { (Get-SafeValue $_.Value 'state') -in @('running','stopping','paused') }).Count -gt 0
    $workerAbsent = Test-WorkerProcessAbsent
    $marker = Read-Json (Join-Path $root 'dist\publication.json')
    $publicationInfo = Get-SafeValue $h 'publication'
    $publicationState = if ($publicationInfo) { [string](Get-SafeValue $publicationInfo 'state') } elseif ($marker) { [string](Get-SafeValue $marker 'state') } else { 'absent' }
    $publicationReady = ($publicationState -eq 'ready')
    # Un marqueur writing ancien peut survivre à une erreur de partage Windows.
    # Deux relevés espacés sans worker permettent au redémarrage propre de le réparer.
    $publicationRepairable = (-not $publicationInfo -and $marker -and (Get-SafeValue $marker 'state') -eq 'writing' -and $pending -eq 0 -and $active -eq 0 -and -not (Get-SafeValue $h 'worker_active' $false) -and $workerAbsent)
    $queueIdle = ($pending -eq 0 -and $active -eq 0)
    $durable = Read-Json (Join-Path $root 'data\collecte-traitement.json')
    $durableLanes = Get-SafeValue $durable 'lanes' ([pscustomobject]@{})
    $stoppedBacklog = $false
    if ($AllowStoppedBacklog -and $active -eq 0 -and $pending -gt 0 -and $workerAbsent) {
        $stoppedBacklog = $true
        foreach ($name in @('collection','indexation','maintenance')) {
            $stoppedLane = Get-SafeValue $durableLanes $name
            if ((Get-SafeValue $stoppedLane 'state') -ne 'cancelled' -or -not (Get-SafeValue $stoppedLane 'stop_requested' $false)) { $stoppedBacklog = $false; break }
        }
    }
    return [pscustomobject]@{
        health = $h; idle = (-not (Get-SafeValue $h 'worker_active' $false) -and ($queueIdle -or $stoppedBacklog) -and -not $laneBusy -and $workerAbsent -and ($publicationReady -or $publicationRepairable -or $stoppedBacklog))
        publication_state = $publicationState
        publication_repairable = [bool]$publicationRepairable
        queue = [pscustomobject]@{ pending=$pending; active=$active; completed=[int](Get-SafeValue $queue 'completed' 0); error=[int](Get-SafeValue $queue 'error' 0) }
        lanes = $lanes; queue_idle = $queueIdle; stopped_backlog=$stoppedBacklog; lane_busy = $laneBusy; worker_absent = $workerAbsent
    }
}

function Wait-ForIdle([string]$Reason, [switch]$AllowStoppedBacklog) {
    $stable = 0
    $observedLaneIds = @{}
    while ($true) {
        try { $snap = Get-IdleSnapshot -AllowStoppedBacklog:$AllowStoppedBacklog }
        catch {
            Write-Log "Arrêt du watcher : serveur arrêté ou identité incorrecte pendant '$Reason' ($($_.Exception.Message))."
            Save-State 'stopped' 'Le serveur a été arrêté ou est devenu inaccessible ; le watcher ne le relance pas.'
            return $null
        }
        if (-not $snap) {
            Write-Log "Échec API transitoire $script:apiFailureCount/3 pendant '$Reason'; nouvel essai au prochain cycle."
            Save-Progress $null $Reason
            Start-Sleep -Seconds $IntervalleSecondes
            continue
        }
        if (@($snap.lanes.PSObject.Properties | Where-Object { (Get-SafeValue $_.Value 'state') -eq 'paused' }).Count -gt 0) {
            Save-Progress $snap.health 'Ressource indisponible ; reprise manuelle nécessaire.'
            Save-State 'stopped' 'Une voie est en pause ; le batch reste arrêté et conserve les points de reprise.'
            Write-AnnualReport $snap.health | Out-Null
            return $null
        }
        if ($snap.idle) { $stable++ } else { $stable = 0 }
        if ($stable -ge 2) {
            $script:state.publication_repairable = $snap.publication_repairable
            $script:state.stopped_backlog = $snap.stopped_backlog
            return $snap
        }
        if ($NePasAttendre) {
            Save-State 'waiting' 'Traitements encore actifs ; option NePasAttendre, watcher arrêté sans soumettre.'
            return $null
        }
        foreach ($property in $snap.lanes.PSObject.Properties) {
            $lane = $property.Value
            $laneId = [string](Get-SafeValue $lane 'id' '')
            $laneState = Get-SafeValue $lane 'state'
            if ($laneState -in @('running','stopping','paused') -and $laneId) { $observedLaneIds[$property.Name] = $laneId }
            elseif ($laneState -eq 'cancelled' -and $laneId -and $observedLaneIds.ContainsKey($property.Name) -and $observedLaneIds[$property.Name] -eq $laneId) {
                Save-State 'stopped' 'Une tâche observée par le watcher a été arrêtée volontairement ; il ne redémarre pas les traitements.'
                return $null
            }
        }
        Save-Progress $snap.health $Reason
        Write-Log ("En attente ({0}) : worker={1}, file={2}/{3}, publication={4}, worker_processus_absent={5}." -f $Reason,(Get-SafeValue $snap.health 'worker_active' $false),$snap.queue.pending,$snap.queue.active,$snap.publication_state,$snap.worker_absent)
        Start-Sleep -Seconds $IntervalleSecondes
    }
}

function Find-MatchingCollection($Health) {
    $lanes = Get-SafeValue $Health 'lanes'
    $lane = Get-SafeValue $lanes 'collection'
    if (-not $lane) { return $null }
    $laneYears = @(Get-SafeValue $lane 'years' @() | ForEach-Object { [int]$_ })
    $sameYears = ($laneYears -join ',') -eq (@($script:state.years) -join ',')
    $samePlan = [string](Get-SafeValue $lane 'plan_id' '') -eq [string]$script:state.plan_id
    if ((Get-SafeValue $lane 'action') -eq 'download' -and $sameYears -and $samePlan) { return $lane }
    return $null
}

function Write-AnnualReport($Health) {
    $lanes = Get-SafeValue $Health 'lanes' ([pscustomobject]@{})
    $lane = Get-SafeValue $lanes 'collection' ([pscustomobject]@{})
    $completed = @(Get-SafeValue $lane 'completed_years' @() | ForEach-Object { [int]$_ })
    $errors = Get-SafeValue $lane 'errors_by_year'
    $runtime = Get-SafeValue $Health 'runtime'
    $driveRoot = [string](Get-SafeValue $runtime 'drive' '')
    $rows = @()
    foreach ($y in $script:state.years) {
        $manifestPath = Join-Path $root ("data\inventaire-{0}-fr.json" -f $y)
        $manifest = Read-Json $manifestPath
        $entries = if ($manifest) { @(Get-SafeValue $manifest 'journals' @()) } else { @() }
        $downloaded = @($entries | Where-Object { (Get-SafeValue $_ 'status') -eq 'telecharge' }).Count
        $driveMissing = @()
        $driveUnknown = @()
        foreach ($entry in $entries) {
            $entryPath = [string](Get-SafeValue $entry 'path' '')
            if ($entryPath -match '^drive/\d{4}/(.+)$') {
                if (-not $driveRoot) { $driveUnknown += [int](Get-SafeValue $entry 'number' 0); continue }
                $candidate = Join-Path (Join-Path $driveRoot ([string]$y)) $Matches[1]
                if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { $driveMissing += [int](Get-SafeValue $entry 'number' 0) }
            }
        }
        $sqlitePath = Join-Path $root ("data\{0}\jort-{0}-fr.sqlite" -f $y)
        $stats = $null
        if (Test-Path -LiteralPath $sqlitePath -PathType Leaf) {
            if ($script:python) {
                $py = @'
import sqlite3, sys, json
p = sys.argv[1]
c = sqlite3.connect('file:' + p.replace('\\', '/') + '?mode=ro', uri=True)
q = c.cursor()
stats = {
    'sources': q.execute('select count(*) from sources').fetchone()[0],
    'extracted_sources': q.execute("select count(*) from sources where extraction_state='terminee'").fetchone()[0],
    'announcements': q.execute('select count(*) from announcements').fetchone()[0],
    'analyzed_announcements': q.execute("select count(*) from announcements where indexation_state='terminee' and coalesce(indexation_error,'')=''").fetchone()[0],
    'analysis_errors': q.execute("select count(*) from announcements where coalesce(indexation_error,'')<>''").fetchone()[0],
    'relations': q.execute('select count(*) from relations').fetchone()[0]
}
print(json.dumps(stats))
c.close()
'@
                $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($py))
                try {
                    $raw = & $script:python -c "import base64;exec(base64.b64decode('$encoded'))" $sqlitePath 2>$null
                    if ($LASTEXITCODE -eq 0 -and $raw) { $stats = $raw | ConvertFrom-Json }
                } catch { }
            }
        }
        $yearError = Get-SafeValue $errors ([string]$y)
        $stateText = if ($null -ne $yearError) { 'échec' } elseif ($y -in $completed) { 'terminée' } else { 'non terminée' }
        $rows += [pscustomobject]@{
            year=$y; state=$stateText; success=($y -in $completed -and $null -eq $yearError); error=$yearError
            manifest_present=[bool]$manifest; inventory_complete=[bool](Get-SafeValue $manifest 'inventory_complete' $false); downloaded_journals=$downloaded
            sqlite=$stats; drive_check=if ($driveRoot) { 'checked' } else { 'unavailable' }
            drive_missing_numbers=@($driveMissing | Where-Object { $_ -gt 0 } | Sort-Object -Unique)
            drive_unchecked_numbers=@($driveUnknown | Where-Object { $_ -gt 0 } | Sort-Object -Unique)
        }
    }
    $publication = Read-Json (Join-Path $root 'dist\publication.json')
    $report = [ordered]@{
        schema_version=1; plan_id=$script:state.plan_id; run_id=$script:state.run_id; years=$script:state.years
        started_at=$script:state.started_at; finished_at=[DateTimeOffset]::Now.ToString('o'); lane_id=$script:state.lane_id
        phase=$script:state.phase; final_lane_state=Get-SafeValue $lane 'state' 'unknown'; publication_state=Get-SafeValue $publication 'state' 'absent'
        progress=$script:state.progress; annual=$rows; drive_root=$driveRoot; log=$script:logPath
    }
    Save-JsonAtomic $reportJsonPath $report
    $md = @('# Rapport de collecte intégrale', '', "Plan : ``$($report.plan_id)``", "Exécution : ``$($report.run_id)``", "Période : $Debut à $Fin", "État final : $($report.final_lane_state)", '', '| Année | État | Journaux téléchargés | Inventaire complet | Extraits / analyses / erreurs | Annonces / liens | PDF Drive absents |', '|---:|---|---:|:---:|---|---|---|')
    foreach ($row in $rows) {
        $dbText = if ($row.sqlite) { "$($row.sqlite.extracted_sources) / $($row.sqlite.analyzed_announcements) / $($row.sqlite.analysis_errors)" } else { 'indisponible' }
        $linkText = if ($row.sqlite) { "$($row.sqlite.announcements) / $($row.sqlite.relations)" } else { 'indisponible' }
        $driveText = if ($row.drive_missing_numbers.Count) { $row.drive_missing_numbers -join ', ' } else { 'aucun repéré' }
        $md += "| $($row.year) | $($row.state) | $($row.downloaded_journals) | $($row.inventory_complete) | $dbText | $linkText | $driveText |"
    }
    [IO.File]::WriteAllLines($reportMdPath, $md, (New-Object Text.UTF8Encoding($false)))
    $script:lastFullReportUtc = [DateTime]::UtcNow
    $script:state['last_full_report_at'] = [DateTimeOffset]::UtcNow.ToString('o')
    Save-JsonAtomic $statePath $script:state
    return $report
}

function Save-Progress($Health, [string]$Reason) {
    $progress = @{}
    if ($Health) {
        $lanes = Get-SafeValue $Health 'lanes' ([pscustomobject]@{})
        $lane = Get-SafeValue $lanes 'collection' ([pscustomobject]@{})
        $queue = Get-SafeValue $Health 'queue' ([pscustomobject]@{})
        $progress = @{
            at=[DateTimeOffset]::Now.ToString('o'); reason=$Reason
            lane_state=Get-SafeValue $lane 'state' 'unknown'; current_year=Get-SafeValue $lane 'current_year' (Get-SafeValue $lane 'year')
            current_number=Get-SafeValue $lane 'current_number'; current=Get-SafeValue $lane 'current' 0; total=Get-SafeValue $lane 'total' 0
            completed_years=@(Get-SafeValue $lane 'completed_years' @()); incomplete_years=@(Get-SafeValue $lane 'incomplete_years' @())
            errors_by_year=Get-SafeValue $lane 'errors_by_year' @{}
            failed_downloads_by_year=Get-SafeValue $lane 'failed_downloads_by_year' @{}
            queue=@{ pending=(Get-SafeValue $queue 'pending' 0); active=(Get-SafeValue $queue 'active' 0); completed=(Get-SafeValue $queue 'completed' 0); error=(Get-SafeValue $queue 'error' 0) }
            worker_active=(Get-SafeValue $Health 'worker_active' $false); publication_state=Get-SafeValue (Get-SafeValue $Health 'publication') 'state' 'unknown'
        }
        $script:lastHealth = $Health
    } else {
        $progress = @{ at=[DateTimeOffset]::Now.ToString('o'); reason=$Reason; api_failures=$script:apiFailureCount }
    }
    $script:state['progress'] = $progress
    $script:state['updated_at'] = [DateTimeOffset]::Now.ToString('o')
    Save-JsonAtomic $statePath $script:state
    $priorReport = Read-Json $reportJsonPath
    $report = [ordered]@{
        schema_version=1; plan_id=$script:state.plan_id; run_id=$script:state.run_id; years=$script:state.years
        phase=$script:state.phase; message=$script:state.message; updated_at=$script:state.updated_at
        lane_id=$script:state.lane_id; progress=$progress
        annual=@(Get-SafeValue $priorReport 'annual' @()); log=$script:logPath
    }
    Save-JsonAtomic $reportJsonPath $report
    if ($Health -and ([DateTime]::UtcNow - $script:lastFullReportUtc).TotalMinutes -ge 5) {
        Write-AnnualReport $Health | Out-Null
    }
}

try {
    New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
    $mutex = New-Object System.Threading.Mutex($false, 'Local\JORT-Collecte-Integrale')
    $ownsMutex = $mutex.WaitOne(0)
    if (-not $ownsMutex) { throw 'Un watcher de collecte intégrale fonctionne déjà.' }
    $fileLock = [IO.File]::Open((Join-Path $stateDir 'watcher.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)

    $prior = Read-Json $statePath
    $priorScope = Get-SafeValue $prior 'scope_id'
    $priorPhase = Get-SafeValue $prior 'phase'
    if ($prior -and $priorScope -eq $scopeId -and $priorPhase -in @('waiting','restarting','submitted','submit_pending','uncertain','watching','stopped')) {
        $script:state = ConvertTo-StateHashtable $prior
        $script:state['years'] = @($script:state['years'] | ForEach-Object { [int]$_ })
        $script:state['publication_repairable'] = [bool](Get-SafeValue $script:state 'publication_repairable' $false)
        $script:state['message'] = [string](Get-SafeValue $script:state 'message' '')
        if ($priorPhase -eq 'stopped') {
            $priorProgress = Get-SafeValue $prior 'progress' ([pscustomobject]@{})
            $cursor = Get-SafeValue $priorProgress 'current_year'
            if ($null -ne $cursor -and [int]$cursor -in $script:state.years) {
                $script:state['resume_from_checkpoint'] = $true
                $script:state['resume_from_year'] = [int]$cursor
                $script:state['phase'] = 'waiting'
                $script:state['message'] = "Reprise demandée au point sauvegardé : année $cursor, sans rescanner les années précédentes."
            } else {
                $script:state['resume_from_checkpoint'] = $false
                Write-Log 'Arrêt précédent sans année de reprise fiable ; une reprise automatique du plan est refusée.'
                Save-State 'stopped' 'Point de reprise absent ou invalide ; aucun traitement soumis.'
                return
            }
        }
        Write-Log "Reprise du watcher pour plan_id=$($script:state.plan_id), run_id=$($script:state.run_id), phase=$($script:state.phase)."
        $lastFull = Get-SafeValue $script:state 'last_full_report_at'
        if ($lastFull) {
            try { $script:lastFullReportUtc = ([DateTimeOffset]::Parse([string]$lastFull)).UtcDateTime } catch { $script:lastFullReportUtc = [DateTime]::MinValue }
        }
    } else {
        $script:state = @{
            schema_version=1; scope_id=$scopeId; run_id=[guid]::NewGuid().ToString('N'); plan_id=$null; years=$years
            phase='waiting'; submit_attempted=$false; lane_id=$null; started_at=[DateTimeOffset]::Now.ToString('o'); updated_at=$null
            message=''; publication_repairable=$false; stopped_backlog=$false; progress=@{}
        }
        $script:state['plan_id'] = 'integrale-' + $script:state.run_id
        Save-State 'waiting' 'Attente de la fin des traitements actuels avant redémarrage et collecte.'
    }

    if ($script:state.phase -in @('submit_pending','uncertain')) {
        if (-not (Ensure-InitialServer)) { return }
        $h = $null
        try {
            while (-not $h) { $h = Get-Health; if (-not $h) { Start-Sleep -Seconds 2 } }
        } catch {
            Save-State 'uncertain' 'Réponse du POST toujours indéterminée ; API inaccessible après trois contrôles. Aucun nouvel envoi ne sera tenté.'
            Write-Log "POST incertain conservé sans renvoi : $($_.Exception.Message)"
            return
        }
        $existing = Find-MatchingCollection $h
        if ($existing) {
            $script:state['lane_id'] = [string](Get-SafeValue $existing 'id' '')
            $existingState = [string](Get-SafeValue $existing 'state' 'unknown')
            if ($existingState -in @('running','stopping','paused')) {
                Save-State 'watching' 'Collecte du plan retrouvée ; suivi repris sans second POST.'
            } elseif ($existingState -eq 'done' -or $existingState -eq 'failed') {
                Save-State 'watching' "Collecte retrouvée avec état $existingState ; attendre la fin de l’analyse et de la publication."
            } else {
                Write-AnnualReport $h | Out-Null
                Save-State 'stopped' "Collecte retrouvée avec état terminal $existingState ; aucun second POST envoyé."
                return
            }
        } else {
            Save-State 'uncertain' 'POST potentiellement reçu mais plan absent de la voie durable ; reprise automatique refusée pour éviter un doublon.'
            Write-Log 'Aucune voie portant le même plan_id; uncertain conservé, aucun second POST.'
            return
        }
    }

    if ($script:state.phase -in @('submitted','watching')) {
        $h = $null
        try { while (-not $h) { $h = Get-Health; if (-not $h) { Start-Sleep -Seconds 2 } } }
        catch { Save-State 'stopped' 'Serveur arrêté après trois contrôles ; watcher arrêté sans relancer les tâches.'; Write-AnnualReport $script:lastHealth | Out-Null; return }
        $lanes = Get-SafeValue $h 'lanes'
        $lane = Get-SafeValue $lanes 'collection'
        if (-not $lane -or [string](Get-SafeValue $lane 'id' '') -ne [string]$script:state.lane_id) {
            if ($script:state.phase -eq 'submitted') {
                $same = Find-MatchingCollection $h
                if ($same) { $script:state['lane_id']=[string](Get-SafeValue $same 'id' ''); $lane=$same }
                else { Save-State 'uncertain' 'La collecte enregistrée ne correspond plus à la voie active ; aucune relance automatique.'; return }
            } else { Save-State 'stopped' 'Serveur ou voie de collecte arrêté volontairement ; watcher arrêté sans relance.'; return }
        }
    }

    if ($script:state.phase -in @('waiting','restarting')) {
        if (-not (Ensure-InitialServer)) { return }
        if ($script:state.phase -eq 'waiting' -and -not [bool](Get-SafeValue $script:state 'resume_from_checkpoint' $false)) {
            $idle = Wait-ForIdle 'traitements actuels' -AllowStoppedBacklog
            if (-not $idle) { return }
            Save-State 'restarting' 'Traitements au repos deux fois de suite ; redémarrage contrôlé pour charger les corrections.'
            $stopper = Join-Path $root 'arreter-jort.ps1'
            $launcher = Join-Path $root 'lancer-jort.ps1'
            & $stopper -SansNotification
            Write-Log 'Préparation globale de la base : sauvegarde, cohérence, regroupements et optimisation.'
            & $script:python (Join-Path $root 'scripts\preparer_collecte.py') 2>&1 | ForEach-Object { Write-Log ([string]$_) }
            if ($LASTEXITCODE -ne 0) { throw 'Préparation de la base en échec ; aucun batch de collecte lancé.' }
            & $launcher -SansNavigateur
            $ready = $false
            for ($i=0; $i -lt 90; $i++) {
                try { $h = Get-Health; if ($h.service -eq 'jort-local') { $ready=$true; break } } catch { }
                Start-Sleep -Seconds 1
            }
            if (-not $ready) { throw 'Le serveur ne répond pas après le redémarrage.' }
        } else {
            try { $null = Get-Health } catch { Save-State 'stopped' 'Serveur arrêté pendant le redémarrage ; watcher fermé sans le relancer.'; return }
            if ([bool](Get-SafeValue $script:state 'resume_from_checkpoint' $false)) {
                Write-Log "Reprise directe au curseur, année $($script:state.resume_from_year) ; préparation globale et inventaires antérieurs ignorés."
            }
        }
        $idle = Wait-ForIdle 'après redémarrage' -AllowStoppedBacklog
        if (-not $idle) { return }
        if ([bool](Get-SafeValue $script:state 'resume_from_checkpoint' $false)) {
            $idleLanes = Get-SafeValue $idle.health 'lanes' ([pscustomobject]@{})
            $idleCollection = Get-SafeValue $idleLanes 'collection' ([pscustomobject]@{})
            $savedCursor = Get-SafeValue $idleCollection 'current_year' (Get-SafeValue $idleCollection 'year')
            if ($null -eq $savedCursor -or -not ($script:state.years -contains [int]$savedCursor)) {
                Save-State 'stopped' 'Le serveur ne fournit pas de curseur valide ; aucune reprise soumise.'
                return
            }
            $script:state['resume_from_year'] = [int]$savedCursor
            Write-Log "Curseur durable confirmé par le serveur : année $savedCursor."
        }
        Save-State 'submit_pending' 'Envoi imminent ; état persistant écrit avant le POST pour protéger la reprise.'
        $script:state.submit_attempted = $true
        Save-JsonAtomic $statePath $script:state
        $bodyValue = @{ action='download'; years=@($script:state.years); limit=0; plan_id=[string]$script:state.plan_id }
        if ([bool](Get-SafeValue $script:state 'resume_from_checkpoint' $false)) {
            $bodyValue['resume_from_year'] = [int]$script:state.resume_from_year
        }
        $body = $bodyValue | ConvertTo-Json -Compress
        try {
            $result = Invoke-RestMethod -Uri ($api + '/api/collecte/job') -Method Post -Headers $header -ContentType 'application/json' -Body $body -TimeoutSec 20
            $resultJob = Get-SafeValue $result 'job'
            $resultLanes = Get-SafeValue $resultJob 'lanes'
            $lane = Get-SafeValue $resultLanes 'collection'
            $confirmedYears = @(Get-SafeValue $lane 'years' @() | ForEach-Object { [int]$_ })
            if (-not $lane -or (Get-SafeValue $lane 'action') -ne 'download' -or [string](Get-SafeValue $lane 'plan_id' '') -ne [string]$script:state.plan_id -or ($confirmedYears -join ',') -ne (@($script:state.years) -join ',')) {
                throw 'La réponse ne permet pas de confirmer le plan soumis.'
            }
        } catch {
            Save-State 'uncertain' "Réponse du POST perdue ou non confirmée : $($_.Exception.Message). Aucun nouvel envoi automatique."
            Write-Log "POST incertain conservé avec plan_id=$($script:state.plan_id); aucune seconde soumission."
            Write-AnnualReport $script:lastHealth | Out-Null
            return
        }
        $script:state['lane_id'] = [string](Get-SafeValue $lane 'id' '')
        $script:state['resume_from_checkpoint'] = $false
        Save-State 'watching' 'Collecte soumise une seule fois ; suivi de la file durable et de l analyse parallèle.'
        Write-Log ("Collecte soumise : lane_id={0}, années={1}." -f $script:state.lane_id, ($script:state.years -join ','))
    }

    while ($script:state.phase -eq 'watching') {
        try { $h = Get-Health } catch {
            Save-State 'stopped' 'Le serveur est resté inaccessible après trois contrôles ; watcher fermé sans le relancer.'
            Write-AnnualReport $script:lastHealth | Out-Null
            Write-Log 'Serveur disparu pendant le suivi ; aucune relance automatique.'
            return
        }
        if (-not $h) {
            Save-Progress $null 'Échec API transitoire pendant le suivi.'
            Start-Sleep -Seconds $IntervalleSecondes
            continue
        }
        $lanes = Get-SafeValue $h 'lanes'
        $lane = Get-SafeValue $lanes 'collection'
        if (-not $lane -or [string](Get-SafeValue $lane 'id' '') -ne [string]$script:state.lane_id) {
            Save-State 'stopped' 'La voie a changé ou a été arrêtée ; watcher fermé sans relancer de tâche.'
            Write-AnnualReport $h | Out-Null
            return
        }
        $laneState = [string](Get-SafeValue $lane 'state' 'unknown')
        if ($laneState -in @('cancelled','stopping','paused')) {
            Save-State 'stopped' "La collecte a été arrêtée volontairement ou mise en pause ($laneState); watcher fermé."
            Write-AnnualReport $h | Out-Null
            return
        }
        $idle = Wait-ForIdle 'collecte intégrale'
        if (-not $idle) { return }
        $fresh = Get-SafeValue (Get-SafeValue $idle.health 'lanes') 'collection'
        $freshState = [string](Get-SafeValue $fresh 'state' 'unknown')
        if ($fresh -and [string](Get-SafeValue $fresh 'id' '') -eq [string]$script:state.lane_id -and $freshState -in @('done','failed')) {
            Save-Progress $idle.health "Fin de la collecte et de la file d’analyse."
            $outcome = Get-PlanOutcome $idle.health
            Save-State $outcome "Collecte terminée ($outcome) ; consulter les fichiers restant à reprendre dans le rapport."
            $report = Write-AnnualReport $idle.health
            Write-Log "Collecte terminée : état=$($fresh.state), rapport=$reportJsonPath."
            break
        }
        Save-Progress $h 'suivi collecte intégrale'
        Write-Log ("Suivi : voie={0}, année={1}, file={2}/{3}." -f $laneState,(Get-SafeValue $lane 'current_year'),(Get-SafeValue (Get-SafeValue $h 'queue') 'pending' 0),(Get-SafeValue (Get-SafeValue $h 'queue') 'active' 0))
        Start-Sleep -Seconds $IntervalleSecondes
    }
} catch {
    Write-Log "ERREUR : $($_.Exception.Message)"
    if ($script:state) {
        if ($script:state.phase -in @('submit_pending','uncertain')) {
            Save-State -Phase 'uncertain' -Message ([string]$_.Exception.Message)
        } else {
            Save-State -Phase 'failed' -Message ([string]$_.Exception.Message)
        }
        try { Write-AnnualReport $script:lastHealth | Out-Null } catch { Write-Log "Rapport annuel incomplet : $($_.Exception.Message)" }
    }
    throw
} finally {
    if ($fileLock) { $fileLock.Dispose() }
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    if ($mutex) { $mutex.Dispose() }
}

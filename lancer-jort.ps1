param([switch]$SansNavigateur)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\jort-lifecycle.ps1')

$lockStream = $null
$startedProcess = $null
$previousEnvironment = @{}
$stage = 'Contexte Windows'
try {
    # Drive appartient au Bureau courant : aucun changement de compte ni élévation.
    $session = Get-JortSessionContext
    $lockStream = Enter-JortLaunchLock
    Write-Host "JO · Session : $($session.Name) · Projet : $($JortPaths.Root)"
    $reconciliationPattern=[regex]::Escape((Join-Path $JortPaths.Root 'scripts\reconcilier_entites_postgresql.py'))
    $reconciliationProcesses=@(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction Stop |
        Where-Object { [string]$_.CommandLine -match $reconciliationPattern -and [string]$_.CommandLine -notmatch '--status|--pause|--help' })
    if ($reconciliationProcesses.Count) { throw 'Réconciliation active : attendre sa fin ou demander une pause avant de rouvrir le site.' }

    $stage = 'Accès au projet et dépendances'
    foreach ($required in @($JortPaths.Server, (Join-Path $JortPaths.Root 'dist\collecte.html'), (Join-Path $JortPaths.Root 'dist\index.html'))) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Fichier nécessaire absent : $required" }
    }
    $python = Resolve-JortPython
    $databaseConfiguration = Join-Path $JortPaths.Root 'data\config\base.json'
    if (Test-Path -LiteralPath $databaseConfiguration) {
        $database = Get-Content -LiteralPath $databaseConfiguration -Raw | ConvertFrom-Json
        if ($database.engine -eq 'postgresql' -and $database.state -eq 'active') {
            Assert-JortLocalPath (Join-Path $JortPaths.Root 'data\postgresql')
            & $python (Join-Path $JortPaths.Root 'scripts\postgresql_local.py') start
            if ($LASTEXITCODE -ne 0) { throw 'Base PostgreSQL indisponible.' }
        }
    }
    $expectedBuild = Get-JortBuild $python
    $probe = Join-Path $JortPaths.Logs ('.lancement-' + [guid]::NewGuid().ToString('N') + '.tmp')
    try { [IO.File]::WriteAllText($probe, 'JORT') }
    finally { if (Test-Path -LiteralPath $probe) { Remove-Item -LiteralPath $probe -Force } }

    $stage = 'Accès Google Drive'
    $driveRoot = if ($env:JORT_GOOGLE_DRIVE_ROOT) { $env:JORT_GOOGLE_DRIVE_ROOT } else { 'G:\Mon Drive' }
    if (-not [IO.Path]::IsPathRooted($driveRoot)) { throw 'JORT_GOOGLE_DRIVE_ROOT doit contenir un chemin absolu vers Mon Drive.' }
    $driveRoot = [IO.Path]::GetFullPath($driveRoot).TrimEnd('\')
    $jortDrive = Join-Path $driveRoot 'JORT'
    $driveAvailable = $false
    try {
        # Vérifier aussi l'ouverture d'un PDF, au-delà de son existence.
        $folders = @($jortDrive) + @([IO.Directory]::GetDirectories($jortDrive) | Where-Object { [IO.Path]::GetFileName($_) -match '^\d{4}$' })
        foreach ($folder in $folders) {
            $sample = [IO.Directory]::EnumerateFiles($folder, '*.pdf') | Select-Object -First 1
            if ($sample) {
                $stream = [IO.File]::Open($sample, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
                try { $null = $stream.ReadByte() } finally { $stream.Dispose() }
                break
            }
        }
        $driveProbe = Join-Path $jortDrive ('.jort-lancement-' + [guid]::NewGuid().ToString('N') + '.tmp')
        try { [IO.File]::WriteAllText($driveProbe, 'JORT') }
        finally { if ([IO.File]::Exists($driveProbe)) { [IO.File]::Delete($driveProbe) } }
        $driveAvailable = $true
    } catch {
        Write-Warning "Drive '$jortDrive' inaccessible pour '$($session.Name)' : $($_.Exception.Message). Le site démarre quand même ; les transferts vers Drive seront à reprendre quand G: sera disponible. Les PDF locaux seront conservés."
    }
    if ($driveAvailable) { Write-Host "JO · Drive vérifié : $jortDrive" }
    $runtimeDirectory = Join-Path $JortPaths.Root 'tmp\runtime'
    Assert-JortLocalPath $runtimeDirectory

    $stage = 'Contrôle du serveur existant'
    $ready = $false
    if (Test-JortPort) {
        try { $response = Get-JortHealth } catch { throw "Port 8765 occupé ; contrôle JORT impossible : $($_.Exception.Message)" }
        $serverRecord = Read-JortRecord $JortPaths.ServerRecord
        if (-not (Test-JortListenerRecord $serverRecord) -or
            (-not (Test-JortProcessIdentity $serverRecord 'server') -and -not (Test-JortApiOwnership $response $serverRecord))) {
            throw 'Le serveur du port 8765 ne correspond pas au processus enregistré pour ce projet.'
        }
        if (-not (Test-JortProcessSession $serverRecord $session)) {
            throw 'Le serveur existant utilise un autre compte ou une autre session Windows. Arrêtez-le avec le raccourci Arrêter JORT puis relancez depuis votre Bureau.'
        }
        $configurationMatches = $response.PSObject.Properties['runtime'] -and
            [string]::Equals([string]$response.runtime.drive,$jortDrive,[StringComparison]::OrdinalIgnoreCase) -and
            [string]::Equals([string]$response.runtime.temp,$runtimeDirectory,[StringComparison]::OrdinalIgnoreCase) -and
            [string]::Equals([string]$response.runtime.python,$python,[StringComparison]::OrdinalIgnoreCase)
        if ($response.build -eq $expectedBuild -and $configurationMatches) { $ready = $true }
        else {
            if ($response.worker_active -or ($response.PSObject.Properties['job'] -and $response.job.state -in @('running','stopping'))) {
                throw "Une tâche est active ; la version ou les chemins nécessitent un redémarrage. Arrêtez-la depuis l'administration avant de relancer le site."
            }
            Stop-JortOwnedProcess $serverRecord 'server' 20
        }
    }

    if (-not $ready) {
        $stage = 'Arrêt des processus résiduels'
        foreach ($entry in @(@($JortPaths.ServerRecord,'server'), @($JortPaths.WorkerRecord,'worker'))) {
            $record = Read-JortRecord $entry[0]
            if ($record -and (Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue)) {
                Stop-JortOwnedProcess $record $entry[1] 10
            }
        }
        $stage = 'Nettoyage des traces temporaires'
        Clear-JortRuntimeArtifacts
        $runtimeDirectory = Join-Path $JortPaths.Root 'tmp\runtime'
        New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
        # Le serveur et ses enfants héritent des temporaires internes au projet.
        $environmentValues = @{ TEMP = $runtimeDirectory; TMP = $runtimeDirectory; PYTHONDONTWRITEBYTECODE = '1'; PYTHONUNBUFFERED = '1'; JORT_GOOGLE_DRIVE_ROOT = $driveRoot }
        foreach ($name in $environmentValues.Keys) {
            $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
            [Environment]::SetEnvironmentVariable($name, $environmentValues[$name], 'Process')
        }
        $stage = 'Préparation de la consultation'
        $consultation = & $python (Join-Path $JortPaths.Root 'scripts\preparer_consultation.py')
        if ($LASTEXITCODE -ne 0) { throw 'La vue de consultation ne peut pas être préparée. Consultez le contrôle de la base.' }
        Write-Host 'JO · Consultation et index de recherche cohérents'
        $stage = 'Démarrage API et contrôles de cohérence'
        $stdout = Join-Path $JortPaths.Logs 'serveur-sortie.log'
        $stderr = Join-Path $JortPaths.Logs 'serveur-erreurs.log'
        $startedProcess = Start-Process -FilePath $python -ArgumentList @('"' + $JortPaths.Server + '"','JO-JORT-API-COLLECTE') -WorkingDirectory $JortPaths.Root -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        $deadline = [DateTime]::UtcNow.AddSeconds(90)
        while ([DateTime]::UtcNow -lt $deadline) {
            $startedProcess.Refresh()
            if ($startedProcess.HasExited) { throw "Le serveur s'est arrêté (code $($startedProcess.ExitCode)). Consultez $stderr." }
            $response = $null
            try { $response = Get-JortHealth } catch { }
            if ($response -and $response.build -eq $expectedBuild) {
                $serverRecord = Read-JortRecord $JortPaths.ServerRecord
                # Le processus API publie son PID, sa racine et sa version.
                # C'est plus fiable que l'arbre de processus du venv, qui
                # peut déléguer à un interpréteur enfant sous Windows.
                $apiStartedHere = $serverRecord -and
                    (Get-Process -Id ([int]$serverRecord.pid) -ErrorAction SilentlyContinue) -and
                    $response.service -eq 'jort-local' -and
                    [int]$response.pid -eq [int]$serverRecord.pid -and
                    [string]::Equals([IO.Path]::GetFullPath([string]$response.root),$JortPaths.Root,[StringComparison]::OrdinalIgnoreCase)
                if (-not $apiStartedHere) {
                    throw 'La réponse API ne correspond pas au processus lancé dans votre session.'
                }
                $ready = $true
                break
            }
            Start-Sleep -Milliseconds 500
        }
        if (-not $ready) { throw "Serveur non prêt après 90 secondes. Consultez $stderr." }
    }

    $stage = 'Validation finale'
    $health = Get-JortHealth
    if ($health.build -ne $expectedBuild) { throw 'La version du serveur a changé pendant le lancement.' }
    if (-not $health.PSObject.Properties['runtime'] -or
        -not [string]::Equals([string]$health.runtime.drive,$jortDrive,[StringComparison]::OrdinalIgnoreCase) -or
        -not [string]::Equals([string]$health.runtime.temp,$runtimeDirectory,[StringComparison]::OrdinalIgnoreCase) -or
        -not [string]::Equals([string]$health.runtime.python,$python,[StringComparison]::OrdinalIgnoreCase)) {
        throw 'Les chemins Python, Drive ou temporaires du serveur ne correspondent pas au lancement.'
    }
    if ($health.startup_check.application_preflight.state -eq 'failed') {
        throw ("Le contrôle de cohérence a échoué : " + ($health.startup_check.application_preflight.warnings -join '; '))
    }
    if ($health.startup_check.state -eq 'attention') { Write-Warning "Contrôles de cohérence à examiner dans le voyant général de l'administration." }
    # L’OCR reste facultatif : aucun traitement PDF n’est lancé ici.
    try {
    $pdfium = & $python -c 'import importlib.util; print(int(importlib.util.find_spec(''pypdfium2'') is not None))'
    $tesseract = Get-Command tesseract -ErrorAction SilentlyContinue
    if (-not $tesseract -and (Test-Path -LiteralPath 'C:\Program Files\Tesseract-OCR\tesseract.exe')) { $tesseract = [pscustomobject]@{ Source = 'C:\Program Files\Tesseract-OCR\tesseract.exe' } }
    if (-not $tesseract -or "$pdfium".Trim() -ne '1') { Write-Warning 'OCR indisponible : les PDF scannés nécessitent Tesseract et pypdfium2. Les PDF textuels restent utilisables.' }
    else {
        $languages = & $tesseract.Source --list-langs | Out-String
        if ($LASTEXITCODE -ne 0 -or $languages -notmatch '(?m)^fra\s*$' -or $languages -notmatch '(?m)^ara\s*$') { Write-Warning 'Vérifiez les langues OCR fra et ara.' }
    }
    } catch { Write-Warning "Contrôle OCR facultatif impossible : $($_.Exception.Message)" }
    Write-Host "JO · Site et API prêts : $($JortPaths.Url)"
    $stage = 'Ouverture du navigateur'
    if (-not $SansNavigateur) {
        try { Start-Process ($JortPaths.Url + 'collecte.html') }
        catch { Write-Warning "Serveur prêt ; ouvrez manuellement $($JortPaths.Url)collecte.html : $($_.Exception.Message)" }
    }
} catch {
    $failureMessage = $_.Exception.Message
    # Seul le serveur créé par ce lancement peut être arrêté ici.
    if ($startedProcess) {
        $startedProcess.Refresh()
        if (-not $startedProcess.HasExited) {
            $temporaryRecord = [pscustomobject]@{ pid = $startedProcess.Id; started_epoch = ([DateTimeOffset]$startedProcess.StartTime.ToUniversalTime()).ToUnixTimeSeconds(); python = $python }
            try { Stop-JortOwnedProcess $temporaryRecord 'server' 15 } catch { Write-Warning $_.Exception.Message }
        }
    }
    $message = "Impossible de lancer JORT — $stage : $failureMessage"
    if ($SansNavigateur) { throw $message }
    try { (New-Object -ComObject WScript.Shell).Popup($message,0,'JORT privé',16) | Out-Null } catch { Write-Error $message }
    exit 1
} finally {
    foreach ($name in $previousEnvironment.Keys) { [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process') }
    if ($lockStream) { $lockStream.Dispose() }
}

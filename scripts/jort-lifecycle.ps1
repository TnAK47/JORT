# Fonctions communes aux lanceurs et à l'arrêt du service local JORT.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-JortPaths {
    $root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    $logs = Join-Path $root 'data\logs'
    return [pscustomobject]@{
        Root = $root
        Logs = $logs
        Url = 'http://127.0.0.1:8765/'
        Server = Join-Path $root 'scripts\serveur_local.py'
        ServerRecord = Join-Path $logs 'serveur-processus.json'
        WorkerRecord = Join-Path $logs 'travailleur-processus.json'
        Lock = Join-Path $logs 'lancement.lock'
    }
}

function Resolve-JortPython {
    $candidates = @()
    $missingPdfLibrary = @()
    $venv = Join-Path $script:JortPaths.Root '.venv\Scripts\python.exe'
    $bundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $venv) { $candidates += $venv }
    if (Test-Path -LiteralPath $bundled) { $candidates += $bundled }
    $systemPython = Get-Command python -ErrorAction SilentlyContinue
    if ($systemPython) { $candidates += $systemPython.Source }
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate)) { continue }
        & $candidate -c 'import sys; assert sys.version_info >= (3, 10)' 2>$null
        if ($LASTEXITCODE -eq 0) {
            & $candidate -c 'import pypdf' 2>$null
            if ($LASTEXITCODE -ne 0) { $missingPdfLibrary += $candidate; continue }
            return [IO.Path]::GetFullPath($candidate)
        }
    }
    if ($pyLauncher) {
        $resolvedPython = & $pyLauncher.Source -3 -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $resolvedPython) {
            $resolvedPython = ([string]$resolvedPython).Trim()
            & $resolvedPython -c 'import sys; assert sys.version_info >= (3, 10)' 2>$null
            if ($LASTEXITCODE -eq 0) {
                & $resolvedPython -c 'import pypdf' 2>$null
                if ($LASTEXITCODE -eq 0) { return [IO.Path]::GetFullPath($resolvedPython) }
                $missingPdfLibrary += $resolvedPython
            }
        }
    }
    if ($missingPdfLibrary.Count) { throw "Le module pypdf manque dans les interpréteurs trouvés : $($missingPdfLibrary -join ', '). Installez-le avec `"$($missingPdfLibrary[0])`" -m pip install pypdf." }
    throw 'Python 3.10 ou plus récent avec pypdf est introuvable. Installez Python et pypdf, puis relancez JORT.'
}

function Get-JortBuild([string]$Python) {
    # N'utiliser que des apostrophes Python : Windows PowerShell 5.1 retire parfois
    # les guillemets doubles d'un argument natif transmis à python.exe -c.
    $code = 'import hashlib,pathlib,sys; print(hashlib.sha256(b''''.join(p.read_bytes() for p in sorted((pathlib.Path(sys.argv[1])/''scripts'').glob(''*.py'')))).hexdigest())'
    $value = & $Python -c $code $script:JortPaths.Root
    if ($LASTEXITCODE -ne 0 -or -not $value) { throw 'Impossible de calculer la version des scripts JORT.' }
    return ([string]$value).Trim()
}

function Test-JortPythonExecutable([string]$Recorded, [string]$Actual) {
    try {
        $recordedPath = [IO.Path]::GetFullPath($Recorded).TrimEnd('\')
        $actualPath = [IO.Path]::GetFullPath($Actual).TrimEnd('\')
        if ([string]::Equals($recordedPath,$actualPath,[StringComparison]::OrdinalIgnoreCase)) { return $true }
        # Le python du venv peut déléguer au runtime Codex tout en gardant le
        # même projet et la même commande. Cette paire est la seule exception
        # admise ; toute autre divergence reste refusée.
        $venv = [IO.Path]::GetFullPath((Join-Path $script:JortPaths.Root '.venv\Scripts\python.exe')).TrimEnd('\')
        $runtime = [IO.Path]::GetFullPath((Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')).TrimEnd('\')
        return ([string]::Equals($recordedPath,$venv,[StringComparison]::OrdinalIgnoreCase) -and
                [string]::Equals($actualPath,$runtime,[StringComparison]::OrdinalIgnoreCase))
    } catch { return $false }
}

function Test-JortPort {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync('127.0.0.1', 8765)
        try { return ($task.Wait(700) -and $client.Connected) } catch { return $false }
    } finally { $client.Dispose() }
}

function Get-JortProcessInfo([int]$Id) {
    try { return Get-CimInstance Win32_Process -Filter "ProcessId = $Id" -ErrorAction Stop } catch { return $null }
}

function Get-JortSessionContext {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Lancez JORT depuis votre session Windows habituelle, sans Exécuter en tant qu'administrateur : le lecteur Drive dépend de cette session."
    }
    $sessionId = (Get-Process -Id $PID).SessionId
    try { $desktops = @(Get-CimInstance Win32_Process -Filter "Name = 'explorer.exe' AND SessionId = $sessionId" -ErrorAction Stop) }
    catch { throw "Impossible de vérifier le Bureau Windows pour '$($identity.Name)'. Lancez le raccourci depuis votre session personnelle : $($_.Exception.Message)" }
    if (-not $desktops.Count) { throw 'Aucune session Bureau Windows trouvée pour ce lancement. Utilisez le raccourci depuis le Bureau où Google Drive est connecté.' }
    $sameUser = $false
    foreach ($desktop in $desktops) {
        $owner = Invoke-CimMethod -InputObject $desktop -MethodName GetOwnerSid -ErrorAction Stop
        if ($owner.ReturnValue -eq 0 -and $owner.Sid -eq $identity.User.Value) { $sameUser = $true }
    }
    if (-not $sameUser) { throw "Le compte '$($identity.Name)' ne correspond pas au Bureau Windows. Lancez le raccourci JORT depuis votre propre session." }
    return [pscustomobject]@{ Name = $identity.Name; Sid = $identity.User.Value; SessionId = $sessionId }
}

function Test-JortProcessSession($Record, $Context) {
    try {
        $info = Get-JortProcessInfo ([int]$Record.pid)
        if (-not $info -or $info.SessionId -ne $Context.SessionId) { return $false }
        $owner = Invoke-CimMethod -InputObject $info -MethodName GetOwnerSid -ErrorAction Stop
        return ($owner.ReturnValue -eq 0 -and $owner.Sid -eq $Context.Sid)
    } catch { return $false }
}

function Get-JortHealth {
    $health = Invoke-RestMethod -Uri ($script:JortPaths.Url + 'api/health') -TimeoutSec 3
    if ($health.service -ne 'jort-local') { throw 'Le port 8765 appartient à un autre service.' }
    # Compatibilité pour mettre à jour une instance ancienne, une seule lecture complète.
    if (-not $health.PSObject.Properties['build']) {
        return Invoke-RestMethod -Uri ($script:JortPaths.Url + 'api/collecte/status') -TimeoutSec 10
    }
    return $health
}

function Test-JortListenerRecord($Record) {
    try {
        $listeners = @(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction Stop)
        return ($listeners.Count -gt 0 -and @($listeners | Where-Object { $_.OwningProcess -ne [int]$Record.pid -or $_.LocalAddress -ne '127.0.0.1' }).Count -eq 0)
    } catch { return $false }
}

function Test-JortProcessIdentity($Record, [ValidateSet('server','worker')][string]$Role) {
    try {
        if (-not $Record) { return $false }
        $pidProperty = $Record.PSObject.Properties['pid']
        $pythonProperty = $Record.PSObject.Properties['python']
        $epochProperty = $Record.PSObject.Properties['started_epoch']
        if (-not $pidProperty -or -not $pythonProperty -or -not $epochProperty) { return $false }
        $id = [int]$pidProperty.Value
        if ($id -le 0 -or -not $pythonProperty.Value -or [double]$epochProperty.Value -le 0) { return $false }
        $processInfo = Get-JortProcessInfo $id
    } catch { return $false }
    if (-not $processInfo) { return $false }
    try {
        $recordExe = [IO.Path]::GetFullPath([string]$pythonProperty.Value).TrimEnd('\')
        $processExe = [IO.Path]::GetFullPath([string]$processInfo.ExecutablePath).TrimEnd('\')
        if (-not (Test-JortPythonExecutable $recordExe $processExe)) { return $false }
        $actualEpoch = ([DateTimeOffset](Get-Process -Id $id -ErrorAction Stop).StartTime.ToUniversalTime()).ToUnixTimeSeconds()
        if ([Math]::Abs($actualEpoch - [double]$epochProperty.Value) -gt 10) { return $false }
    } catch { return $false }

    $command = [string]$processInfo.CommandLine
    $expectedScript = if ($Role -eq 'server') { $script:JortPaths.Server } else { Join-Path $script:JortPaths.Root 'scripts\construire_base_2024.py' }
    $expectedScript = [IO.Path]::GetFullPath($expectedScript)
    $scriptToken = '(?i)(?:^|[\s"])' + [regex]::Escape($expectedScript) + '(?=$|[\s"])'
    if ($command -notmatch $scriptToken) { return $false }
    if ($command.IndexOf([IO.Path]::GetFullPath($script:JortPaths.Root),[StringComparison]::OrdinalIgnoreCase) -lt 0) { return $false }
    return $true
}

function Test-JortApiOwnership($Status, $Record) {
    # Repli si CIM masque la ligne de commande : exige l'API JORT, les chemins
    # internes au projet, le même exécutable, PID et heure de démarrage.
    try {
        if (-not $Status -or $Status.service -ne 'jort-local' -or -not $Record) { return $false }
        if ($Status.PSObject.Properties['root'] -and $Status.PSObject.Properties['pid']) {
            if ([int]$Status.pid -ne [int]$Record.pid -or
                -not [string]::Equals([IO.Path]::GetFullPath([string]$Status.root), $script:JortPaths.Root, [StringComparison]::OrdinalIgnoreCase) -or
                -not (Test-JortListenerRecord $Record)) { return $false }
        } else {
        $archive = [IO.Path]::GetFullPath([string]$Status.paths.archive)
        $inbox = [IO.Path]::GetFullPath([string]$Status.paths.inbox)
        $downloads = [IO.Path]::GetFullPath([string]$Status.paths.downloads)
        $root = [IO.Path]::GetFullPath($script:JortPaths.Root).TrimEnd('\')
        $prefix = $root + [IO.Path]::DirectorySeparatorChar
        foreach ($path in @($archive,$inbox,$downloads)) {
            if (-not $path.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) { return $false }
        }
        if (-not (Test-JortListenerRecord $Record)) { return $false }
        }
        $id = [int]$Record.pid
        $process = Get-Process -Id $id -ErrorAction Stop
        $recordExe = [IO.Path]::GetFullPath([string]$Record.python).TrimEnd('\')
        $processExe = [IO.Path]::GetFullPath([string]$process.Path).TrimEnd('\')
        if (-not (Test-JortPythonExecutable $recordExe $processExe)) { return $false }
        $actualEpoch = ([DateTimeOffset]$process.StartTime.ToUniversalTime()).ToUnixTimeSeconds()
        if ([Math]::Abs($actualEpoch - [double]$Record.started_epoch) -gt 10) { return $false }
        return $true
    } catch { return $false }
}

function Read-JortRecord([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json } catch { return $null }
}

function Invoke-JortShutdownRequest {
    try {
        Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/collecte/shutdown' -Method Post -Headers @{'X-JORT-Local'='1'; 'Origin'='http://127.0.0.1:8765'} -ContentType 'application/json' -Body '{}' -TimeoutSec 3 | Out-Null
        return $true
    } catch { return $false }
}

function Stop-JortOwnedProcess($Record, [ValidateSet('server','worker')][string]$Role, [int]$TimeoutSeconds = 12) {
    if (-not $Record) { return }
    $pidProperty = $Record.PSObject.Properties['pid']
    if (-not $pidProperty) { throw "L’enregistrement du processus ne contient pas de PID ; arrêt refusé." }
    $id = [int]$pidProperty.Value
    if (-not (Get-Process -Id $id -ErrorAction SilentlyContinue)) { return }
    if (-not (Test-JortProcessIdentity $Record $Role)) {
        if ($Role -eq 'server') {
            $status = $null
            try { $status = Get-JortHealth } catch { }
            if (Test-JortApiOwnership $status $Record) {
                [void](Invoke-JortShutdownRequest)
                try { Wait-Process -Id $id -Timeout $TimeoutSeconds -ErrorAction SilentlyContinue } catch { }
                if (-not (Get-Process -Id $id -ErrorAction SilentlyContinue) -and -not (Test-JortPort)) { return }
                # Revalider l'identité API immédiatement avant le kill.
                $status = $null
                try { $status = Get-JortHealth } catch { }
                if (-not (Test-JortApiOwnership $status $Record)) {
                    # L'arrêt gracieux peut finir entre le premier contrôle et
                    # cette vérification : considérer cela comme un succès.
                    if (-not (Get-Process -Id $id -ErrorAction SilentlyContinue) -and -not (Test-JortPort)) { return }
                    throw "L'identité du serveur JORT $id n'est plus vérifiable ; arrêt forcé annulé."
                }
                $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
                & $taskkill /PID $id /T /F | Out-Null
                if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $id -ErrorAction SilentlyContinue)) { throw "Impossible d'arrêter le serveur JORT $id." }
                try { Wait-Process -Id $id -Timeout 5 -ErrorAction SilentlyContinue } catch { }
                if ((Get-Process -Id $id -ErrorAction SilentlyContinue) -or (Test-JortPort)) { throw "Le serveur JORT $id ou son port fonctionne encore après l'arrêt." }
                return
            }
        }
        throw "Le PID $id ne correspond pas à un processus JORT identifié ($Role) ; aucun arrêt forcé n'a été tenté."
    }

    if ($Role -eq 'server') { [void](Invoke-JortShutdownRequest) }
    try { Wait-Process -Id $id -Timeout $TimeoutSeconds -ErrorAction SilentlyContinue } catch { }
    if (Get-Process -Id $id -ErrorAction SilentlyContinue) {
        # Revalider juste avant l'arrêt forcé afin d'éviter un PID réutilisé.
        if (-not (Test-JortProcessIdentity $Record $Role)) { throw "L'identité du processus $id a changé ; arrêt forcé annulé." }
        $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
        & $taskkill /PID $id /T /F | Out-Null
        if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $id -ErrorAction SilentlyContinue)) { throw "Impossible d'arrêter le processus JORT $id." }
        try { Wait-Process -Id $id -Timeout 5 -ErrorAction SilentlyContinue } catch { }
    }
    if (Get-Process -Id $id -ErrorAction SilentlyContinue) { throw "Le processus JORT $id fonctionne encore." }
}

function Clear-JortRuntimeArtifacts {
    if (Test-JortPort) { throw 'Nettoyage refusé : le port du serveur est encore actif.' }
    # Un script lancé directement peut ne pas avoir enregistré son PID.
    $rootPattern = [regex]::Escape($script:JortPaths.Root + '\scripts\')
    $running = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction Stop | Where-Object { $_.CommandLine -match $rootPattern })
    if ($running.Count) { throw 'Nettoyage refusé : un programme Python du projet fonctionne encore.' }
    # Nettoyage limité aux traces d'exécution jetables situées dans le projet.
    # Les événements, tickets, PDF, bases, clés et le fichier-verrou sont conservés.
    $root = [IO.Path]::GetFullPath($script:JortPaths.Root).TrimEnd('\')
    $separator = [IO.Path]::DirectorySeparatorChar
    $runtimeFiles = @(
        'data\logs\serveur-sortie.log', 'data\logs\serveur-erreurs.log',
        'data\logs\serveur-processus.json', 'data\logs\travailleur-processus.json',
        'data\logs\serveur.pid', 'data\logs\travailleur.pid',
        'tmp\iort-page.html', 'tmp\jal-2024-151.pdf',
        'tmp\pdfs\jal-149-page-2.png', 'tmp\pdfs\jal-150-page-13.png'
    )
    foreach ($relative in $runtimeFiles) {
        $path = [IO.Path]::GetFullPath((Join-Path $root $relative))
        Assert-JortLocalPath $path
        if (-not $path.StartsWith($root + $separator,[StringComparison]::OrdinalIgnoreCase)) { throw 'Nettoyage annulé : un chemin sort du dossier du projet.' }
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            try { Remove-Item -LiteralPath $path -Force -ErrorAction Stop }
            catch { Write-Warning "Trace temporaire conservée jusqu'au prochain arrêt : $relative" }
        }
    }

    $dataRoot = Join-Path $root 'data'
    Assert-JortLocalPath $dataRoot
    if (Test-Path -LiteralPath $dataRoot -PathType Container) {
        foreach ($yearDirectory in Get-ChildItem -LiteralPath $dataRoot -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^\d{4}$' }) {
            $flag = Join-Path $yearDirectory.FullName 'logs\annulation.flag'
            Assert-JortLocalPath $flag
            if (Test-Path -LiteralPath $flag -PathType Leaf) { Remove-Item -LiteralPath $flag -Force }
        }
    }

    # Les écritures atomiques laissent ces fichiers cachés seulement si un processus
    # a été interrompu. Ils sont reconnus par le préfixe et l'extension utilisés.
    foreach ($relativeRoot in @('data','dist','documents','tmp')) {
        $searchRoot = Join-Path $root $relativeRoot
        Assert-JortLocalPath $searchRoot
        if (-not (Test-Path -LiteralPath $searchRoot -PathType Container)) { continue }
        # Parcours explicite : ne pas suivre les jonctions vers un autre disque.
        $pending = New-Object 'System.Collections.Generic.Stack[string]'
        $pending.Push($searchRoot)
        while ($pending.Count) {
            $directory = $pending.Pop()
            if ((Get-Item -LiteralPath $directory -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
            foreach ($child in Get-ChildItem -LiteralPath $directory -Force -ErrorAction Stop) {
                if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
                if ($child.PSIsContainer) { $pending.Push($child.FullName); continue }
                if ($child.Name -notmatch '^\..+-.+\.tmp$') { continue }
                $file = $child
                $path = [IO.Path]::GetFullPath($file.FullName)
                if (-not $path.StartsWith($root + $separator,[StringComparison]::OrdinalIgnoreCase)) { throw 'Nettoyage annulé : un fichier temporaire sort du dossier du projet.' }
                try { Remove-Item -LiteralPath $path -Force -ErrorAction Stop }
                catch { Write-Warning "Fichier temporaire verrouillé, conservé pour le prochain arrêt : $($file.Name)" }
            }
        }
    }

    # Répertoire réservé à TEMP/TMP pour les prochains processus JORT.
    $runtimeDirectory = Join-Path $root 'tmp\runtime'
    Assert-JortLocalPath $runtimeDirectory
    if (Test-Path -LiteralPath $runtimeDirectory -PathType Container) {
        $item = Get-Item -LiteralPath $runtimeDirectory -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Nettoyage refusé : tmp/runtime est une jonction.' }
        $links = @(Get-ChildItem -LiteralPath $runtimeDirectory -Force -Recurse | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint })
        if ($links.Count) { throw 'Nettoyage refusé : tmp/runtime contient une jonction ou un lien.' }
        if (-not ([IO.Path]::GetFullPath($runtimeDirectory)).StartsWith($root + $separator,[StringComparison]::OrdinalIgnoreCase)) { throw 'Répertoire temporaire hors projet.' }
        try { Remove-Item -LiteralPath $runtimeDirectory -Recurse -Force -ErrorAction Stop }
        catch { Write-Warning 'Le répertoire temporaire contient encore un fichier verrouillé ; il sera réutilisé puis nettoyé au prochain arrêt.' }
    }

    foreach ($relativeDirectory in @('tmp\pdfs','tmp')) {
        $temporaryDirectory = Join-Path $root $relativeDirectory
        Assert-JortLocalPath $temporaryDirectory
        if ((Test-Path -LiteralPath $temporaryDirectory -PathType Container) -and -not (Get-ChildItem -LiteralPath $temporaryDirectory -Force | Select-Object -First 1)) {
            Remove-Item -LiteralPath $temporaryDirectory -Force
        }
    }
}

function Assert-JortLocalPath([string]$Path) {
    $root = [IO.Path]::GetFullPath($script:JortPaths.Root).TrimEnd('\')
    $full = [IO.Path]::GetFullPath($Path)
    if (-not $full.StartsWith($root + '\',[StringComparison]::OrdinalIgnoreCase)) { throw "Chemin hors projet : $full" }
    $current = $root
    foreach ($segment in @('') + $full.Substring($root.Length + 1).Split('\')) {
        if ($segment) { $current = Join-Path $current $segment }
        if ((Test-Path -LiteralPath $current) -and ((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Chemin JORT redirigé par un lien ou une jonction : $current"
        }
    }
}

function Enter-JortLaunchLock {
    Assert-JortLocalPath $script:JortPaths.Lock
    New-Item -ItemType Directory -Path $script:JortPaths.Logs -Force | Out-Null
    try { return [IO.File]::Open($script:JortPaths.Lock,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None) }
    catch { throw 'Un lancement ou un arrêt JORT est déjà en cours. Attendez quelques secondes et réessayez.' }
}

$script:JortPaths = Get-JortPaths

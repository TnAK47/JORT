param([string]$LockProbePath)
$ErrorActionPreference = 'Stop'
$helper = Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts\jort-lifecycle.ps1'
. $helper

if ($LockProbePath) {
    $probePath = [IO.Path]::GetFullPath($LockProbePath)
    $script:JortPaths = [pscustomobject]@{
        Root = Split-Path -Parent (Split-Path -Parent $probePath)
        Logs = Split-Path -Parent $probePath
        Url = 'http://127.0.0.1:8765/'
        Server = ''
        ServerRecord = ''
        WorkerRecord = ''
        Lock = $probePath
    }
    try { $probeStream = Enter-JortLaunchLock; $probeStream.Dispose(); exit 3 }
    catch { exit 0 }
}

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

$syntaxFiles = @(
    (Join-Path (Split-Path -Parent $PSScriptRoot) 'lancer-jort.ps1'),
    (Join-Path (Split-Path -Parent $PSScriptRoot) 'arreter-jort.ps1'),
    (Join-Path (Split-Path -Parent $PSScriptRoot) 'demarrer.ps1'),
    $helper
)
foreach ($path in $syntaxFiles) {
    $tokens = $null
    $parseErrors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$parseErrors)
    Assert-True ($parseErrors.Count -eq 0) "Erreur de syntaxe dans $path : $($parseErrors -join '; ')"
}

$testRoot = Join-Path $env:TEMP ('jort-lifecycle-mock-' + [Guid]::NewGuid().ToString('N'))
$testLogs = Join-Path $testRoot 'logs'
New-Item -ItemType Directory -Path $testLogs -Force | Out-Null
$script:JortPaths = [pscustomobject]@{
    Root = $testRoot
    Logs = $testLogs
    Url = 'http://127.0.0.1:8765/'
    Server = Join-Path $testRoot 'scripts\serveur_local.py'
    ServerRecord = Join-Path $testLogs 'serveur-processus.json'
    WorkerRecord = Join-Path $testLogs 'travailleur-processus.json'
    Lock = Join-Path $testLogs 'lancement.lock'
}

# Exécute le calcul d’empreinte avec le vrai Python bundled et compare avec .NET,
# sans lancer le serveur ni utiliser les données du projet.
$projectRoot = Split-Path -Parent $PSScriptRoot
$bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
Assert-True (Test-Path -LiteralPath $bundledPython) 'Le Python bundled requis pour vérifier Get-JortBuild est absent.'
$script:JortPaths.Root = $projectRoot
$pythonBuild = Get-JortBuild $bundledPython
$joinedScripts = New-Object IO.MemoryStream
foreach ($pythonFile in (Get-ChildItem -LiteralPath (Join-Path $projectRoot 'scripts') -Filter '*.py' -File | Sort-Object -Property Name -CaseSensitive)) {
    $bytes = [IO.File]::ReadAllBytes($pythonFile.FullName)
    $joinedScripts.Write($bytes,0,$bytes.Length)
}
$sha256 = [Security.Cryptography.SHA256]::Create()
$expectedBuild = ([BitConverter]::ToString($sha256.ComputeHash($joinedScripts.ToArray()))).Replace('-','').ToLowerInvariant()
$sha256.Dispose()
$joinedScripts.Dispose()
Assert-True ($pythonBuild -eq $expectedBuild) 'Get-JortBuild doit correspondre à la concaténation ordonnée des scripts Python.'
$script:JortPaths.Root = $testRoot

$script:MockProcessInfo = $null
$script:MockProcessExists = $true
$script:WaitCalled = $false
$script:MockActualEpoch = 0
function Get-JortProcessInfo([int]$Id) { return $script:MockProcessInfo }
function Get-Process {
    param([int]$Id,[string]$ErrorAction)
    if (-not $script:MockProcessExists) { return $null }
    return [pscustomobject]@{ Id = $Id; StartTime = [DateTimeOffset]::FromUnixTimeSeconds([long]$script:MockActualEpoch).UtcDateTime }
}
function Wait-Process {
    param([int]$Id,[int]$Timeout,[string]$ErrorAction)
    $script:WaitCalled = $true
    throw 'Wait-Process ne doit jamais être appelé pour un processus mock étranger.'
}

$mockExe = Join-Path $testRoot 'python.exe'
$scriptFile = $script:JortPaths.Server
$epoch = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$script:MockRecord = [pscustomobject]@{ pid = 42424; started_epoch = $epoch; python = $mockExe }
$script:MockActualEpoch = $epoch
$script:MockProcessInfo = [pscustomobject]@{
    ExecutablePath = $mockExe
    CommandLine = '"' + $mockExe + '" "' + $scriptFile + '"'
}

Assert-True (Test-JortProcessIdentity $script:MockRecord 'server') 'Un processus mock qui correspond devrait être reconnu.'

# PID absent, PID réutilisé, exécutable remplacé et commande/racine étrangère doivent tous être rejetés.
$savedProcessInfo = $script:MockProcessInfo
$script:MockProcessInfo = $null
Assert-True (-not (Test-JortProcessIdentity $script:MockRecord 'server')) 'Un PID absent doit être rejeté.'
$script:MockProcessInfo = $savedProcessInfo

$script:MockRecord.started_epoch = $epoch - 60
Assert-True (-not (Test-JortProcessIdentity $script:MockRecord 'server')) 'Un PID réutilisé avec une époque différente doit être rejeté.'
$script:MockRecord.started_epoch = $epoch

$script:MockProcessInfo = [pscustomobject]@{ ExecutablePath = (Join-Path $testRoot 'other-python.exe'); CommandLine = $savedProcessInfo.CommandLine }
Assert-True (-not (Test-JortProcessIdentity $script:MockRecord 'server')) 'Un chemin exécutable différent doit être rejeté.'
$script:MockProcessInfo = [pscustomobject]@{ ExecutablePath = $mockExe; CommandLine = 'python.exe C:\other-project\scripts\serveur_local.py' }
Assert-True (-not (Test-JortProcessIdentity $script:MockRecord 'server')) 'Une ligne de commande appartenant à un autre projet doit être rejetée.'
$script:MockProcessInfo = [pscustomobject]@{ ExecutablePath = $mockExe; CommandLine = '"' + $mockExe + '" "' + $scriptFile + '.foreign"' }
Assert-True (-not (Test-JortProcessIdentity $script:MockRecord 'server')) "Un chemin de script qui ne correspond qu’au préfixe doit être rejeté."
Assert-True (-not (Test-JortProcessIdentity ([pscustomobject]@{ pid = 42424 }) 'server')) 'Un enregistrement incomplet doit être rejeté sans erreur.'

# L'arrêt doit s'arrêter sur le contrôle d'identité, avant toute attente ou commande de terminaison.
$script:WaitCalled = $false
$script:MockProcessInfo = $null
$stopRejected = $false
try { Stop-JortOwnedProcess $script:MockRecord 'server' 0 } catch { $stopRejected = $true }
Assert-True $stopRejected 'Un PID absent doit refuser l’arrêt.'
Assert-True (-not $script:WaitCalled) 'Un PID absent ne doit pas lancer l’arrêt.'

$script:MockProcessInfo = $savedProcessInfo
$script:MockRecord.started_epoch = $epoch - 60
$stopRejected = $false
try { Stop-JortOwnedProcess $script:MockRecord 'server' 0 } catch { $stopRejected = $true }
Assert-True $stopRejected 'Un PID réutilisé avec une époque différente doit refuser l’arrêt.'
Assert-True (-not $script:WaitCalled) 'Un PID réutilisé ne doit pas lancer l’arrêt.'
$script:MockRecord.started_epoch = $epoch

$script:MockProcessInfo = [pscustomobject]@{ ExecutablePath = $mockExe; CommandLine = 'python.exe C:\other-project\scripts\serveur_local.py' }
$stopRejected = $false
try { Stop-JortOwnedProcess $script:MockRecord 'server' 0 } catch { $stopRejected = $true }
Assert-True $stopRejected 'Une commande étrangère doit refuser l’arrêt.'
Assert-True (-not $script:WaitCalled) 'Une commande étrangère ne doit pas lancer l’arrêt.'

$script:MockProcessInfo = [pscustomobject]@{ ExecutablePath = $mockExe; CommandLine = '"' + $mockExe + '" "' + $scriptFile + '.foreign"' }
$stopRejected = $false
try { Stop-JortOwnedProcess $script:MockRecord 'server' 0 } catch { $stopRejected = $true }
Assert-True $stopRejected "L’arrêt doit refuser un processus dont la commande ne correspond pas."
Assert-True (-not $script:WaitCalled) "Aucune attente/terminaison ne doit être engagée après un échec d’identité."
$script:MockProcessInfo = [pscustomobject]@{ ExecutablePath = $mockExe; CommandLine = '"' + $mockExe + '" "' + $scriptFile + '.foreign"' }
$stopRejected = $false
try { Stop-JortOwnedProcess ([pscustomobject]@{ pid = 42424 }) 'server' 0 } catch { $stopRejected = $true }
Assert-True $stopRejected "Un enregistrement incomplet doit également refuser l’arrêt."
Assert-True (-not $script:WaitCalled) "Un enregistrement incomplet ne doit pas démarrer l’arrêt."

# Le verrou exclusif doit laisser entrer le premier appel et refuser le doublon simultané.
$firstLock = Enter-JortLaunchLock
$powershell = Join-Path $PSHOME 'pwsh.exe'
if (-not (Test-Path -LiteralPath $powershell)) { $powershell = Join-Path $PSHOME 'powershell.exe' }
$childArguments = @('-NoProfile','-File',('"' + $PSCommandPath + '"'),'-LockProbePath',('"' + $script:JortPaths.Lock + '"'))
$child = Start-Process -FilePath $powershell -ArgumentList $childArguments -Wait -PassThru -WindowStyle Hidden
Assert-True ($child.ExitCode -eq 0) 'Un deuxième PowerShell simultané doit être refusé par le verrou exclusif.'
$firstLock.Dispose()

# Nettoie seulement le dossier temporaire créé par ce test.
if ([IO.Path]::GetFullPath($testRoot).StartsWith([IO.Path]::GetFullPath($env:TEMP),[StringComparison]::OrdinalIgnoreCase)) {
    Remove-Item -LiteralPath (Join-Path $testLogs 'lancement.lock') -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $testLogs -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $testRoot -Force -ErrorAction SilentlyContinue
}

Write-Output "OK : syntaxe PowerShell 5.1, identité PID/époque/exécutable/commande, refus d’arrêt étranger et verrou de lancement."

param(
    [ValidateSet('Plan','Appliquer','Retour')][string]$Action='Plan',
    [switch]$AvecIndex,
    [switch]$NettoyerVersionsMortes
)
$ErrorActionPreference='Stop'
$jortRoot=$PSScriptRoot
$jortPython=Join-Path $jortRoot '.venv\Scripts\python.exe'
$jortScript=Join-Path $jortRoot 'scripts\ameliorer_performance_jort.py'
if (-not (Test-Path -LiteralPath $jortPython)) { throw 'Python du projet absent.' }
if (($AvecIndex -or $NettoyerVersionsMortes) -and $Action -ne 'Appliquer') {
    throw 'Les options de maintenance nécessitent -Action Appliquer.'
}
$jortArguments=@('-B','-u',$jortScript)
if ($Action -eq 'Appliquer') {
    . (Join-Path $jortRoot 'scripts\jort-lifecycle.ps1')
    if (Test-JortPort) { throw 'Arrêter le site et ses batchs avec arreter-jort.ps1 avant application.' }
    & $jortPython -B (Join-Path $jortRoot 'scripts\postgresql_local.py') start
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL indisponible. Aucun correctif installé.' }
    $jortArguments+='--apply'
    if ($AvecIndex) { $jortArguments+='--indexes' }
    if ($NettoyerVersionsMortes) { $jortArguments+='--vacuum' }
} elseif ($Action -eq 'Retour') { $jortArguments+='--rollback' }
& $jortPython @jortArguments
if ($LASTEXITCODE -ne 0) { throw 'Opération interrompue. Consulter le message et data\maintenance\performance\installation.json si présent.' }

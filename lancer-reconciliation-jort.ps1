param(
    [ValidateSet('Simulation','Appliquer','Reprendre','Etat','Pause')]
    [string]$Action='Simulation',
    [ValidateRange(0,1000000)][int]$LimiteGroupes=0,
    [switch]$RetenterErreurs
)
$ErrorActionPreference='Stop'
$root=$PSScriptRoot
. (Join-Path $root 'scripts\jort-lifecycle.ps1')
$python=Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Environnement .venv absent ; utiliser le lanceur habituel pour rétablir les dépendances.' }
$scriptPath=Join-Path $root 'scripts\reconcilier_entites_postgresql.py'
$arguments=@('-B','-u',$scriptPath,'--interval-minutes','20')
if ($RetenterErreurs -and $Action -ne 'Reprendre') { throw 'RetenterErreurs nécessite -Action Reprendre.' }
switch ($Action) {
    'Etat' { $arguments+='--status' }
    'Pause' { $arguments+='--pause' }
    'Appliquer' { $arguments+='--apply' }
    'Reprendre' { $arguments+=@('--apply','--resume') }
}
if ($RetenterErreurs) { $arguments+='--retry-errors' }
if ($LimiteGroupes -gt 0) { $arguments+=@('--limit-groups',[string]$LimiteGroupes) }
if ($Action -notin @('Etat','Pause')) {
    if ($Action -in @('Appliquer','Reprendre') -and (Test-JortPort)) {
        throw 'Arrêtez JORT avec arreter-jort.ps1 avant les corrections. Ce lanceur démarrera PostgreSQL seul.'
    }
    # Réutiliser un plan simulé pour éviter une seconde lecture intégrale.
    $reportPath=Join-Path $root 'data\maintenance\reconciliation-entites\rapport.json'
    if ($Action -eq 'Appliquer' -and (Test-Path -LiteralPath $reportPath)) {
        $report=Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
        if ($report.phase -eq 'planned') { $arguments+='--resume' }
        elseif ($report.phase -in @('paused','applying','failed','done_with_issues')) {
            throw 'Un plan est déjà disponible : utiliser -Action Reprendre pour conserver les points de reprise.'
        }
    }
    & $python -B (Join-Path $root 'scripts\postgresql_local.py') start
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL indisponible. Aucune réconciliation démarrée.' }
}
$oldTitle=$Host.UI.RawUI.WindowTitle
try {
    $Host.UI.RawUI.WindowTitle='JO-JORT · Réconciliation · '+$Action
    Write-Host ('JO-JORT · '+$Action+' · suivi toutes les 20 minutes · aucun appel à un modèle')
    & $python @arguments
    $resultCode=$LASTEXITCODE
    if ($resultCode -eq 2) { Write-Warning 'Passage terminé avec des groupes à vérifier : consulter le rapport.' }
    elseif ($resultCode -ne 0) { throw 'Traitement interrompu ; lire data\maintenance\reconciliation-entites\etat.json avant reprise.' }
} finally { $Host.UI.RawUI.WindowTitle=$oldTitle }

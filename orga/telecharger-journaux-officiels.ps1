param(
    [string]$Destination = 'H:\Mon Drive\JORT\journaux-officiels',
    [int]$Debut = 1956,
    [int]$Fin = (Get-Date).Year,
    [switch]$Inventaire,
    [switch]$Actualiser,
    [int]$Limite = 0,
    [string]$Python
)
$ErrorActionPreference = 'Stop'
if (-not $Python) {
    $bundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundled) { $Python = $bundled }
    else { $Python = (Get-Command python -ErrorAction Stop).Source }
}
$arguments = @((Join-Path $PSScriptRoot 'collecter_journaux_officiels.py'),
    '--destination', $Destination, '--debut', "$Debut", '--fin', "$Fin", '--limite', "$Limite")
if ($Inventaire) { $arguments += '--inventaire' }
if ($Actualiser) { $arguments += '--actualiser' }
Write-Host "JORT lois et décrets · arabe et français · $Debut à $Fin"
Write-Host "Destination : $Destination"
& $Python -u @arguments
if ($LASTEXITCODE -ne 0) { throw "Collecte interrompue ou incomplète (code $LASTEXITCODE). Consulter orga/data/collecte-officielle/rapport.json." }

param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath,
    [switch]$Replace
)

$ErrorActionPreference = 'Stop'
if (-not $Replace) {
    throw 'La restauration remplace toute la base locale. Relancez avec -Replace après avoir arrêté JORT.'
}
$projectRoot = [IO.Path]::GetFullPath($PSScriptRoot)
. (Join-Path $projectRoot 'scripts\jort-lifecycle.ps1')
$script:JortPaths = Get-JortPaths
$python = Resolve-JortPython

& $python (Join-Path $projectRoot 'scripts\restaurer_postgresql.py') --backup $BackupPath --replace
if ($LASTEXITCODE -ne 0) {
    throw 'La restauration PostgreSQL a echoue ; la base doit etre verifiee avant tout demarrage de JORT.'
}

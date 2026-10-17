param(
    [string]$GoogleDriveRoot = $env:JORT_GOOGLE_DRIVE_ROOT
)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath($PSScriptRoot)
. (Join-Path $projectRoot 'scripts\jort-lifecycle.ps1')
$script:JortPaths = Get-JortPaths
$python = Resolve-JortPython

$arguments = @((Join-Path $projectRoot 'scripts\sauvegarder_postgresql_drive.py'))
if (-not [string]::IsNullOrWhiteSpace($GoogleDriveRoot)) {
    $arguments += @('--drive-root', $GoogleDriveRoot)
}
& $python @arguments
if ($LASTEXITCODE -ne 0) {
    throw 'La sauvegarde PostgreSQL a echoue. Aucun fichier incomplet ne sera conserve comme sauvegarde valide.'
}

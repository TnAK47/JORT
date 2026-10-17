param(
    [string]$GoogleDriveRoot = $env:JORT_GOOGLE_DRIVE_ROOT
)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath($PSScriptRoot)
. (Join-Path $projectRoot 'scripts\jort-lifecycle.ps1')
$script:JortPaths = Get-JortPaths
$python = Resolve-JortPython
if ([string]::IsNullOrWhiteSpace($GoogleDriveRoot)) {
    $GoogleDriveRoot = 'G:\Mon Drive'
}
if (-not (Test-Path -LiteralPath $GoogleDriveRoot -PathType Container)) {
    throw "Dossier Google Drive introuvable : $GoogleDriveRoot. Vérifiez que Google Drive pour ordinateur est connecté et synchronisé."
}
$driveRoot = [IO.Path]::GetFullPath($GoogleDriveRoot)
if ($driveRoot.StartsWith($projectRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Le dossier Google Drive doit être extérieur au projet JORT.'
}

# Une sauvegarde pendant une écriture pourrait être incohérente. Exiger l'arrêt du site.
try {
    $status = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/collecte/status' -TimeoutSec 2
    if ($status.service -eq 'jort-local') {
        throw 'Arrêtez JORT avant de sauvegarder les bases.'
    }
} catch {
    if ($_.Exception.Message -like '*Arrêtez JORT*') { throw }
    # Pas de serveur local : les bases peuvent être copiées.
}

$databaseFiles = @(Get-ChildItem -LiteralPath (Join-Path $projectRoot 'data') -Directory | ForEach-Object {
    Get-ChildItem -LiteralPath $_.FullName -File -Filter 'jort-*-fr.sqlite'
})
if ($databaseFiles.Count -eq 0) { throw 'Aucune base SQLite annuelle trouvée sous data/.' }

$dateFolder = Join-Path $driveRoot ('JORT\sauvegardes-base\' + (Get-Date -Format 'yyyy-MM-dd'))
$snapshot = Join-Path $dateFolder (Get-Date -Format 'HHmmss')
New-Item -ItemType Directory -Path $snapshot -Force | Out-Null
$pythonCode = 'import sqlite3,sys; src=sqlite3.connect(sys.argv[1]); dst=sqlite3.connect(sys.argv[2]); src.backup(dst); result=dst.execute("pragma integrity_check").fetchone()[0]; dst.close(); src.close(); print(result); sys.exit(0 if result == "ok" else 1)'
$inventory = @()
foreach ($source in $databaseFiles) {
    $year = Split-Path -Leaf (Split-Path -Parent $source.FullName)
    $yearFolder = Join-Path $snapshot $year
    New-Item -ItemType Directory -Path $yearFolder -Force | Out-Null
    $destination = Join-Path $yearFolder $source.Name
    $integrity = & $python -c $pythonCode $source.FullName $destination
    if ($LASTEXITCODE -ne 0 -or [string]$integrity -notmatch '^ok$' -or -not (Test-Path -LiteralPath $destination -PathType Leaf)) {
        throw "Échec de sauvegarde de la base $year. Copie partielle conservée dans $snapshot."
    }
    $sourceHash = (Get-FileHash -LiteralPath $source.FullName -Algorithm SHA256).Hash
    $backupHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
    $inventory += [PSCustomObject]@{ year = $year; file = $source.Name; bytes = (Get-Item -LiteralPath $destination).Length; sourceSha256 = $sourceHash; backupSha256 = $backupHash; integrity = 'ok' }
}
$inventory | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $snapshot 'inventaire.json') -Encoding UTF8
Write-Output "Bases annuelles sauvegardées et vérifiées : $snapshot"
Write-Output "Nombre de bases : $($inventory.Count)"

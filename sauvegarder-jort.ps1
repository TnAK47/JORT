param(
    [Parameter(Mandatory = $true)]
    [string]$Destination
)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$targetRoot = [IO.Path]::GetFullPath($Destination)
if ($targetRoot.StartsWith($projectRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Choisissez un dossier de sauvegarde situé hors du projet JORT.'
}
if ([IO.Path]::GetPathRoot($targetRoot) -eq [IO.Path]::GetPathRoot($projectRoot)) {
    throw 'La sauvegarde doit être placée sur un autre lecteur ou un emplacement réseau.'
}

$statusUrl = 'http://127.0.0.1:8765/api/collecte/status'
try {
    $status = Invoke-RestMethod -Uri $statusUrl -TimeoutSec 2
    if ($status.service -eq 'jort-local') {
        throw 'Arrêtez complètement JORT avant la sauvegarde afin de copier un état cohérent sans modification concurrente.'
    }
} catch {
    if ($_.Exception.Message -like '*Arrêtez complètement JORT*') { throw }
    # Serveur arrêté : les fichiers persistants peuvent être copiés directement.
}

$snapshot = Join-Path $targetRoot ('JORT-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $snapshot -Force | Out-Null
$items = @(
    'data', 'documents', 'dist', 'scripts', 'requirements.txt', 'README.md',
    'AGENTS.md', 'UTILISATION-COLLECTE.md', 'AVANCEMENT.md', 'lancer-jort.ps1',
    'arreter-jort.ps1', 'demarrer.ps1', 'creer-raccourci-bureau.ps1',
    'creer-raccourci-arret.ps1'
)
foreach ($item in $items) {
    $source = Join-Path $projectRoot $item
    if (-not (Test-Path -LiteralPath $source)) { continue }
    $destinationPath = Join-Path $snapshot $item
    if ((Get-Item -LiteralPath $source).PSIsContainer) {
        New-Item -ItemType Directory -Path $destinationPath -Force | Out-Null
        & robocopy.exe $source $destinationPath /E /COPY:DAT /DCOPY:DAT /R:2 /W:2 /NFL /NDL /NJH /NJS | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "Échec de copie de $item (code robocopy $LASTEXITCODE). La copie partielle reste dans $snapshot." }
    } else {
        $parent = Split-Path -Parent $destinationPath
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $destinationPath
    }
}

$inventory = @()
foreach ($item in $items) {
    $source = Join-Path $projectRoot $item
    if (-not (Test-Path -LiteralPath $source)) { continue }
    $sourceFiles = if ((Get-Item -LiteralPath $source).PSIsContainer) {
        Get-ChildItem -LiteralPath $source -File -Recurse
    } else {
        Get-Item -LiteralPath $source
    }
    foreach ($file in $sourceFiles) {
        $relative = $file.FullName.Substring($projectRoot.Length).TrimStart('\')
        $backupFile = Join-Path $snapshot $relative
        if (-not (Test-Path -LiteralPath $backupFile -PathType Leaf)) { throw "Fichier absent de la sauvegarde : $relative" }
        $sourceHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
        $backupHash = (Get-FileHash -LiteralPath $backupFile -Algorithm SHA256).Hash
        if ($sourceHash -ne $backupHash) { throw "Vérification SHA-256 échouée : $relative" }
        $inventory += [PSCustomObject]@{ path = $relative; bytes = $file.Length; sha256 = $sourceHash }
    }
}
$inventory | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $snapshot 'inventaire-verification.json') -Encoding UTF8
Write-Output "Sauvegarde créée sur l’autre emplacement : $snapshot"
Write-Output "Fichiers copiés et empreintes SHA-256 enregistrées : $($inventory.Count)"
Write-Output 'Pour restaurer, arrêtez JORT puis comparez les fichiers avec inventaire-verification.json avant toute copie.'

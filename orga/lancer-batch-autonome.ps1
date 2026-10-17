$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython) {
  $py = $venvPython
} else {
  $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
  if (-not $pythonCommand) {
    throw 'Python est requis. Créez .venv ou installez Python puis relancez ce script.'
  }
  $py = $pythonCommand.Source
}
$log = Join-Path $PSScriptRoot 'data\batch-autonome.log'
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
function Write-Log($line) { Add-Content -LiteralPath $log -Value ("[{0}] {1}" -f (Get-Date -Format s), $line) }
Write-Log 'Démarrage batch autonome local.'
try {
  & $py -u (Join-Path $PSScriptRoot 'collecter_journaux_officiels.py') 2>&1 | ForEach-Object { Write-Log $_ }
  $downloadCode = $LASTEXITCODE
  Write-Log "Téléchargement terminé, code=$downloadCode"
} catch { Write-Log "Erreur téléchargement: $($_.Exception.Message)" }
try {
  & $py -u (Join-Path $PSScriptRoot 'extraire_textes_officiels.py') 2>&1 | ForEach-Object { Write-Log $_ }
  $extractCode = $LASTEXITCODE
  Write-Log "Extraction terminée, code=$extractCode"
} catch { Write-Log "Erreur extraction: $($_.Exception.Message)" }
Write-Log 'Batch autonome terminé.'

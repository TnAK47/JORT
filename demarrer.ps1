$ErrorActionPreference = 'Stop'
$launcher = Join-Path $PSScriptRoot 'lancer-jort.ps1'
$stopper = Join-Path $PSScriptRoot 'arreter-jort.ps1'

& $launcher -SansNavigateur
Write-Host 'JORT privé : http://127.0.0.1:8765 — Ctrl+C pour arrêter.'
try {
    while ($true) {
        Start-Sleep -Seconds 2
        $status = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/health' -TimeoutSec 3
        if ($status.service -ne 'jort-local') { throw 'Le port 8765 ne répond pas comme le service JORT.' }
    }
} finally {
    # PowerShell exécute enfin même quand Ctrl+C interrompt la boucle, sans callback .NET.
    & $stopper -SansNotification
}

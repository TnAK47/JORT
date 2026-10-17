$ErrorActionPreference='Stop'
$flag=Join-Path $PSScriptRoot 'data\maintenance\pause-migration-postgresql.flag'
New-Item -ItemType File -Path $flag -Force | Out-Null
Write-Output 'Pause demandee : la migration finit le journal courant, enregistre son point de reprise, puis s arrete.'

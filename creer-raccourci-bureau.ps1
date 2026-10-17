$ErrorActionPreference = 'Stop'
$desktopFolder = [Environment]::GetFolderPath('Desktop')
if (-not $desktopFolder) { throw 'Le dossier Bureau est introuvable.' }
$shortcutPath = Join-Path $desktopFolder 'JORT privé.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $PSScriptRoot 'lancer-jort.ps1') + '"'
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.Description = 'Ouvrir JORT privé. Les collectes sont lancées manuellement depuis le site.'
$shortcut.IconLocation = (Join-Path $env:SystemRoot 'System32\shell32.dll') + ',14'
$shortcut.Save()
Write-Output $shortcutPath

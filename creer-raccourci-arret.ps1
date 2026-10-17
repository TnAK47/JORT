$ErrorActionPreference = 'Stop'
$desktopFolder = [Environment]::GetFolderPath('Desktop')
if (-not $desktopFolder) { throw 'Le dossier Bureau est introuvable.' }
$shortcutPath = Join-Path $desktopFolder 'Arrêter JORT.lnk'
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
$shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $PSScriptRoot 'arreter-jort.ps1') + '"'
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.Description = "Arrêter le site JORT privé et les traitements qu’il pilote."
$shortcut.IconLocation = (Join-Path $env:SystemRoot 'System32\shell32.dll') + ',27'
$shortcut.Save()
Write-Output $shortcutPath

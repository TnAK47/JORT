param([switch]$SansTelechargement)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$tools = Join-Path $root 'tools'
$archive = Join-Path $root 'data\downloads\postgresql-18.6-windows-x64.zip'
$bin = Join-Path $tools 'pgsql\bin\pg_ctl.exe'
New-Item -ItemType Directory -Path (Split-Path $archive) -Force | Out-Null
New-Item -ItemType Directory -Path $tools -Force | Out-Null
if (-not (Test-Path -LiteralPath $bin)) {
    if (-not (Test-Path -LiteralPath $archive)) {
        if ($SansTelechargement) { throw 'Archive PostgreSQL absente.' }
        # Lien Windows x64 de la page officielle EDB, référencée par PostgreSQL.org.
        Invoke-WebRequest -Uri 'https://sbp.enterprisedb.com/getfile.jsp?fileid=1260609' -OutFile $archive -UseBasicParsing
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($archive)
    try {
        foreach ($entry in $zip.Entries) {
            if ($entry.FullName -notmatch '^pgsql/(bin|lib|share)/') { continue }
            $target = [IO.Path]::GetFullPath((Join-Path $tools $entry.FullName))
            if (-not $target.StartsWith($tools+'\',[StringComparison]::OrdinalIgnoreCase)) { throw "Chemin dangereux dans l'archive." }
            if (-not $entry.Name) { New-Item -ItemType Directory -Path $target -Force | Out-Null; continue }
            New-Item -ItemType Directory -Path (Split-Path $target) -Force | Out-Null
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry,$target,$true)
        }
    } finally { $zip.Dispose() }
}
& (Join-Path $tools 'pgsql\bin\postgres.exe') --version
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL ne peut pas fonctionner sur ce poste.' }
$python = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$venv = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venv)) { & $python -m venv (Join-Path $root '.venv'); if ($LASTEXITCODE -ne 0) { throw "Création de l'environnement Python impossible." } }
$env:TEMP = Join-Path $root 'tmp\installation'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
& $venv -m pip install --disable-pip-version-check -r (Join-Path $root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Installation des dépendances Python échouée.' }

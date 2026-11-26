$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$stateFile = Join-Path $projectRoot 'data\dsh-original-process.json'
if (-not (Test-Path -LiteralPath $stateFile)) { Write-Output 'DSH 原始版没有正在运行的服务记录。'; exit 0 }
$record = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
if ($record.root -ne $projectRoot -or $record.profile -ne 'original') { throw '进程记录与当前目录不一致，未停止任何进程。' }
$owned = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$record.pid)
if ($owned) {
    $running = Get-Process -Id $record.pid -ErrorAction Stop
    if ($owned.Name -ne 'node.exe' -or -not $owned.CommandLine.Contains($record.entry) -or -not $owned.CommandLine.Contains('original') -or $running.StartTime.ToUniversalTime().ToString('o') -ne $record.started) { throw '进程身份已改变，未停止任何进程。' }
    Stop-Process -Id $record.pid
}
Remove-Item -LiteralPath $stateFile
Write-Output 'DSH 原始版已停止。'

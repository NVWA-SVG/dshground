$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$stateFile = Join-Path $projectRoot 'data\windows-dsh-process.json'
if (-not (Test-Path -LiteralPath $stateFile)) { Write-Output '工作台未记录正在运行的服务。'; exit 0 }
$record = Get-Content -LiteralPath $stateFile -Raw -Encoding UTF8 | ConvertFrom-Json
if ($record.root -ne $projectRoot) { throw '进程记录与当前目录不一致，未停止任何进程。' }
$ownedProcess = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$record.pid)
if ($ownedProcess) {
    $running = Get-Process -Id $record.pid -ErrorAction Stop
    if ($ownedProcess.Name -ne 'node.exe' -or -not $ownedProcess.CommandLine.Contains($record.entry) -or -not $ownedProcess.CommandLine.Contains($record.patch) -or $running.StartTime.ToUniversalTime().ToString('o') -ne $record.started) { throw '进程身份已改变，未停止任何进程。' }
    Stop-Process -Id $record.pid
}
Remove-Item -LiteralPath $stateFile
Write-Output '教师工作台服务已停止；已保存课程与材料保留。'

param([switch]$NoWindow)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$env:DSH_HOME = Join-Path $projectRoot 'harness-home'
$env:TEACHER_PREP_ROOT = $projectRoot
$env:DSH_TELEMETRY_MODE = 'OFF'
$serviceUrl = 'http://127.0.0.1:3081/teacher/'
$stateFile = Join-Path $projectRoot 'data\windows-dsh-process.json'
$logDir = Join-Path $projectRoot 'data\windows-logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$boot = $null
try { $boot = Invoke-RestMethod -Uri ($serviceUrl + 'api/bootstrap') -TimeoutSec 2 } catch {}
if ($boot -and $boot.root -ne $projectRoot) { throw '3081 端口上运行着另一个工作目录的教师工作台。' }
if (-not $boot) {
    $portOwner = Get-NetTCPConnection -LocalPort 3081 -State Listen -ErrorAction SilentlyContinue
    if ($portOwner) { throw '3081 端口已被其他程序占用。请关闭占用程序后重试。' }
    $nodeExe = (Get-Command node.exe -ErrorAction Stop).Source
    $entry = Join-Path $projectRoot 'runtime\node_modules\@deepseek-ai\dsh\lib\bin.js'
    $patchPath = Join-Path $projectRoot 'config\windows-teacher.patch.yml'
    if (-not (Test-Path -LiteralPath $entry)) { throw '缺少 Windows DSH 运行时。' }
    $arguments = @(('"' + $entry + '"'),'web','--patch',('"' + $patchPath + '"'),'--no-open','--host','127.0.0.1','--port','3081')
    $process = Start-Process -FilePath $nodeExe -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir 'dsh.stdout.log') -RedirectStandardError (Join-Path $logDir 'dsh.stderr.log')
    @{pid=$process.Id; started=$process.StartTime.ToUniversalTime().ToString('o');root=$projectRoot;entry=$entry;patch=$patchPath} | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath $stateFile
    for ($attempt=0; $attempt -lt 60; $attempt++) {
        Start-Sleep -Milliseconds 500
        if ($process.HasExited) { throw ('DSH 未能启动，请查看 ' + $logDir + '\dsh.stderr.log') }
        try { $boot = Invoke-RestMethod -Uri ($serviceUrl + 'api/bootstrap') -TimeoutSec 1; break } catch {}
    }
    if (-not $boot) { throw ('教师工作台启动超时，请查看 ' + $logDir) }
}
if (-not $NoWindow) {
    $edgeCandidates = @((Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),(Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'))
    $edgeExe = $edgeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($edgeExe) { Start-Process -FilePath $edgeExe -ArgumentList @('--app=' + $serviceUrl,'--new-window') }
    else { Start-Process $serviceUrl }
}
Write-Output $serviceUrl
